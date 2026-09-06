---
layout: post
title: "Merging Concurrent Polls Without Ever Serving a Half-Built Graph"
subtitle: "Fan out to independent backends, then publish the merge with one atomic pointer swap."
date: 2026-06-23 09:00:00 +0200
tags: [go, performance, observability, reliability]
description: >-
  A console that polls host inventory, storage, network and alerting concurrently
  must never show a reader a graph where some sources landed and others did not.
  This walks through why updating shared state field by field under a mutex fails
  silently, and how to publish a fully-built snapshot with a single atomic swap
  instead, with a runnable Go program and a race-tested invariant to prove it.
---

## The problem

A console that polls several independent backends - host inventory, storage pool
health, network link state, active alerts - and merges the results into one
in-memory graph has a correctness problem that has nothing to do with any single
poll being slow or wrong. It is about what a reader is allowed to see while the
merge is in progress.

The obvious first implementation holds one struct, one mutex, and updates whichever
field each fetch goroutine has just finished:

```go
// Broken. Do not copy this.
type Server struct {
	mu      sync.Mutex
	hosts   []Host
	storage []StorageStatus
	links   []LinkState
	alerts  []Alert
}

func (s *Server) refresh(ctx context.Context) {
	var wg sync.WaitGroup
	wg.Add(4)
	go func() {
		defer wg.Done()
		if hosts, err := fetchHosts(ctx); err == nil {
			s.mu.Lock()
			s.hosts = hosts
			s.mu.Unlock()
		}
	}()
	go func() {
		defer wg.Done()
		if alerts, err := fetchAlerts(ctx); err == nil {
			s.mu.Lock()
			s.alerts = alerts
			s.mu.Unlock()
		}
	}()
	// ...storage and links, same shape
	wg.Wait()
}
```

Each field write is properly locked, so nothing here is a data race in the memory
sense. But a reader that takes `s.mu` between the hosts goroutine's unlock and the
alerts goroutine's lock sees the new host list next to the old alert list - a
combination that was never true of the system at any single instant. If a host just
went down and its alert has not landed yet, the graph briefly asserts "host up, no
alerts" and "host up, disk alert firing" as two reads a few microseconds apart,
neither of which is what happened.

This is what makes the bug dangerous rather than merely embarrassing: it does not
crash, deadlock, or show up in `go test -race`, because every field access is
correctly synchronized. What is wrong is not memory safety but atomicity - the merge
is four independent critical sections, and nothing stops a reader interleaving
between any two of them. The bug survives review because the locking looks careful,
survives light load because the interleaving window is microseconds wide, and shows
up in production as a dashboard that occasionally contradicts itself for one poll
cycle - which people either don't notice or blame on the data source.

The same shape appears any time a single logical read must reflect an all-or-nothing
view assembled from more than one independently updated piece of state - struct
fields, map entries, or rows joined across tables refreshed on different schedules.

## Working through it

### Locking narrower or wider does not fix it

The instinct when told "the lock window is too small" is to make it bigger: hold
`s.mu` across the whole `refresh`. That removes the interleaving, but it serializes
the fetches behind the slowest one and blocks every reader for the whole merge -
exactly the coupling that fetching concurrently was meant to avoid. Shrinking each
critical section further does not help either; it narrows the window a reader can
land in without ever closing it. The axis that matters is not how tightly you lock
each field. It is when you make the new state visible at all.

### Build the whole snapshot before anyone can see it

The fix is to stop mutating shared state as results come in. Build a complete new
`Graph` value in variables local to the merge - not reachable from any other
goroutine - and only publish it once every field is final, in one indivisible
step. There are two ways to do that step:

- **`atomic.Pointer[Graph]`**: an atomic word holding a pointer. `Store` replaces
  it in one operation, `Load` reads it in one operation. A reader always gets a
  pointer to some complete `Graph` - the previous one or the new one - never a
  half-written one, because a `Graph`, once published, is never mutated again.
- **A mutex held only for the swap**: `mu.Lock(); current = next; mu.Unlock()`.
  Functionally equivalent. Marginally more expensive on the read path, and it
  reintroduces lock contention exactly where you have the most readers, which
  for a polling console is usually the busiest path in the system.

Either way, the fetches and the merge logic run entirely against local variables.
Nothing behind the published pointer changes shape until the new value replaces
it outright. There is no state a reader can observe that is "in between", because
no such state is ever exposed - it only ever exists in memory a reader has no
reference to.

### Bounding a source that is slow or down

Concurrency buys nothing if one fetch can run forever. Two independent bounds are
needed: a `context.WithTimeout` around each source call, and a policy for what
happens to that source's contribution on failure - which does not have to be the
same policy for every source, and should not silently fail the whole merge.

The policy used below is to keep the previous snapshot's value for that field and
mark it degraded, rather than leaving it empty or aborting. An empty host list is a
worse lie than a stale one: a console that briefly shows yesterday's host list is
more useful than one that shows none. `errgroup.WithContext` runs the four fetches
concurrently under a shared deadline, but every goroutine returns `nil` regardless
of its own fetch's outcome - one source's error must never fail the group or
cancel the other three.

### The swap does not protect a caller who reads the pointer twice

A subtler trap: even with `atomic.Pointer[Graph]` correct on the write side, a
handler that calls `Load()` once per field it needs reopens the same hole the
mutex version had, because the pointer can be swapped between the two `Load()`
calls. The rule is to take the pointer once and read every field off that one
local copy - a cost of one variable, and exactly the kind of thing that quietly
regresses when someone "simplifies" a handler into several small getters later.

### A second, genuine data race, found while building this

While writing the version below, per-source health status was first kept in a
shared `map[string]SourceHealth`, with each fetch goroutine writing its own key
directly. `go test -race` caught it immediately: two goroutines writing to two
different keys of the same map is still unsafe - a Go map gives no per-key
isolation, and an internal resize touches memory shared across every key. The
fix was mechanical: give each goroutine its own local `SourceHealth` variable and
assemble the map from those four variables after `errgroup.Wait()` returns.

The contrast between the two bugs here is the useful part. The mutex-per-field
version is invisible to `-race` because every access is fully synchronized and
merely wrong in sequence - a domain-level atomicity violation. The map-write
version is invisible to a quick read of the code, because each line looks like an
independent, harmless write - a genuine memory race. They need different tools:
an invariant test against the actual read model for the first, the race detector
for the second. This program keeps both.

## The solution

A complete program: `go.mod`, `go.sum`, two source files and a test, runnable
with `go run .` and `go test -race ./...`.

```go
// go.mod
module graphswap

go 1.22

require golang.org/x/sync v0.7.0
```

```
// go.sum
golang.org/x/sync v0.7.0 h1:YsImfSBoP9QPYL0xyKJPq0gcaJdG3rInoqxTWbfQu9M=
golang.org/x/sync v0.7.0/go.mod h1:Czt+wKu1gCyEFDUtn0jG5QVvpJ6rzVqr5aXyt9drQfk=
```

{% raw %}
```go
// graph.go
package main

import (
	"context"
	"encoding/json"
	"hash/fnv"
	"math/rand"
	"time"

	"golang.org/x/sync/errgroup"
)

// Host, StorageStatus, LinkState and Alert stand in for whatever a real
// inventory, storage, network and alerting source would return. The shape
// does not matter for this pattern; only that each source owns its own slice
// and nothing else touches it once fetched.
type Host struct {
	Name  string `json:"name"`
	State string `json:"state"`
}

type StorageStatus struct {
	Pool    string `json:"pool"`
	Healthy bool   `json:"healthy"`
}

type LinkState struct {
	Link string `json:"link"`
	Up   bool   `json:"up"`
}

type Alert struct {
	Source   string `json:"source"`
	Severity string `json:"severity"`
}

// SourceHealth records whether a source's contribution in a given Graph is
// fresh or carried forward from an earlier, successful fetch.
type SourceHealth struct {
	Degraded    bool      `json:"degraded"`
	LastSuccess time.Time `json:"last_success"`
}

// Graph is one complete, self-consistent view of the world. Once built it is
// never mutated - every field is set exactly once before the Graph is
// published, and every reader sees either this whole value or a different
// whole value, never a mix of the two.
type Graph struct {
	Generation uint64                  `json:"generation"`
	BuiltAt    time.Time               `json:"built_at"`
	Hosts      []Host                  `json:"hosts"`
	Storage    []StorageStatus         `json:"storage"`
	Links      []LinkState             `json:"links"`
	Alerts     []Alert                 `json:"alerts"`
	Sources    map[string]SourceHealth `json:"sources"`
	Checksum   uint64                  `json:"checksum"`
}

// Verify recomputes the checksum from the Graph's own fields and compares it
// to the checksum recorded at build time. A mismatch would mean the fields in
// front of us were not all written by the same build - the "torn snapshot"
// this whole design exists to rule out.
func (g *Graph) Verify() bool {
	return g.Checksum == checksum(g.Generation, g.Hosts, g.Storage, g.Links, g.Alerts)
}

func checksum(gen uint64, hosts []Host, storage []StorageStatus, links []LinkState, alerts []Alert) uint64 {
	h := fnv.New64a()
	enc := json.NewEncoder(h)
	_ = enc.Encode(gen)
	_ = enc.Encode(hosts)
	_ = enc.Encode(storage)
	_ = enc.Encode(links)
	_ = enc.Encode(alerts)
	return h.Sum64()
}

const (
	sourceTimeout = 120 * time.Millisecond
	mergeTimeout  = 400 * time.Millisecond
)

// sleepOrDone simulates a slow backend call. It respects ctx so a source that
// has already timed out stops holding its goroutine open waiting on a sleep
// nobody is going to use.
func sleepOrDone(ctx context.Context, d time.Duration) error {
	select {
	case <-time.After(d):
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func randLatency(minMS, maxMS int) time.Duration {
	return time.Duration(minMS+rand.Intn(maxMS-minMS+1)) * time.Millisecond
}

type errStub string

func (e errStub) Error() string { return string(e) }

// maybeFail simulates one source call: it sleeps for a random latency in
// [minMS, maxMS], then fails with probability failRate. Every fetchX below
// is this same shape with different numbers, standing in for whatever a
// real inventory, storage, network or alerting client would do.
func maybeFail(ctx context.Context, minMS, maxMS int, failRate float64, name string) error {
	if err := sleepOrDone(ctx, randLatency(minMS, maxMS)); err != nil {
		return err
	}
	if rand.Float64() < failRate {
		return errStub(name + ": request failed")
	}
	return nil
}

func fetchHosts(ctx context.Context) ([]Host, error) {
	if err := maybeFail(ctx, 20, 180, 0.08, "host inventory"); err != nil {
		return nil, err
	}
	return []Host{{Name: "node-a", State: "ready"}, {Name: "node-b", State: "ready"}}, nil
}

func fetchStorage(ctx context.Context) ([]StorageStatus, error) {
	if err := maybeFail(ctx, 20, 220, 0.08, "storage backend"); err != nil {
		return nil, err
	}
	return []StorageStatus{{Pool: "pool-0", Healthy: true}, {Pool: "pool-1", Healthy: rand.Float64() > 0.1}}, nil
}

func fetchLinks(ctx context.Context) ([]LinkState, error) {
	if err := maybeFail(ctx, 20, 160, 0.08, "network fabric"); err != nil {
		return nil, err
	}
	return []LinkState{{Link: "leaf-1<->spine-1", Up: true}, {Link: "leaf-2<->spine-1", Up: rand.Float64() > 0.05}}, nil
}

func fetchAlerts(ctx context.Context) ([]Alert, error) {
	if err := maybeFail(ctx, 20, 140, 0.08, "alert manager"); err != nil {
		return nil, err
	}
	if rand.Float64() < 0.3 {
		return []Alert{{Source: "storage", Severity: "warning"}}, nil
	}
	return []Alert{}, nil
}

// mergeOnce fetches every source concurrently, bounded by mergeTimeout, and
// builds one brand new Graph in local variables not shared with any reader
// until the caller stores the returned pointer. A source that fails or
// times out falls back to prev's last known good value and is marked
// degraded; it never blocks or corrupts the others.
func mergeOnce(ctx context.Context, prev *Graph, gen uint64) *Graph {
	ctx, cancel := context.WithTimeout(ctx, mergeTimeout)
	defer cancel()

	// Each goroutine owns exactly one pair of variables below, joined into a
	// map only after g.Wait(). A map keyed by source name looks tempting
	// instead, but writing it from four goroutines is a data race even when
	// each writes a different key - a Go map gives no per-key isolation.
	var (
		hosts         []Host
		storage       []StorageStatus
		links         []LinkState
		alerts        []Alert
		hostsHealth   SourceHealth
		storageHealth SourceHealth
		linksHealth   SourceHealth
		alertsHealth  SourceHealth
	)
	now := time.Now()

	g, gctx := errgroup.WithContext(ctx)

	g.Go(func() error {
		sctx, cancel := context.WithTimeout(gctx, sourceTimeout)
		defer cancel()
		data, err := fetchHosts(sctx)
		if err != nil {
			hosts = prev.Hosts
			hostsHealth = SourceHealth{Degraded: true, LastSuccess: prev.Sources["hosts"].LastSuccess}
			return nil // a source failure never fails the merge
		}
		hosts = data
		hostsHealth = SourceHealth{Degraded: false, LastSuccess: now}
		return nil
	})

	g.Go(func() error {
		sctx, cancel := context.WithTimeout(gctx, sourceTimeout)
		defer cancel()
		data, err := fetchStorage(sctx)
		if err != nil {
			storage = prev.Storage
			storageHealth = SourceHealth{Degraded: true, LastSuccess: prev.Sources["storage"].LastSuccess}
			return nil
		}
		storage = data
		storageHealth = SourceHealth{Degraded: false, LastSuccess: now}
		return nil
	})

	g.Go(func() error {
		sctx, cancel := context.WithTimeout(gctx, sourceTimeout)
		defer cancel()
		data, err := fetchLinks(sctx)
		if err != nil {
			links = prev.Links
			linksHealth = SourceHealth{Degraded: true, LastSuccess: prev.Sources["links"].LastSuccess}
			return nil
		}
		links = data
		linksHealth = SourceHealth{Degraded: false, LastSuccess: now}
		return nil
	})

	g.Go(func() error {
		sctx, cancel := context.WithTimeout(gctx, sourceTimeout)
		defer cancel()
		data, err := fetchAlerts(sctx)
		if err != nil {
			alerts = prev.Alerts
			alertsHealth = SourceHealth{Degraded: true, LastSuccess: prev.Sources["alerts"].LastSuccess}
			return nil
		}
		alerts = data
		alertsHealth = SourceHealth{Degraded: false, LastSuccess: now}
		return nil
	})

	_ = g.Wait() // never returns a non-nil error: each goroutine swallows its own

	return &Graph{
		Generation: gen,
		BuiltAt:    now,
		Hosts:      hosts,
		Storage:    storage,
		Links:      links,
		Alerts:     alerts,
		Sources: map[string]SourceHealth{
			"hosts":   hostsHealth,
			"storage": storageHealth,
			"links":   linksHealth,
			"alerts":  alertsHealth,
		},
		Checksum: checksum(gen, hosts, storage, links, alerts),
	}
}
```
{% endraw %}

```go
// main.go
package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"sync/atomic"
	"time"
)

// Merger owns the current Graph. The only mutable state is the pointer
// itself; every Graph it ever points at is immutable from the moment it is
// built.
type Merger struct {
	current atomic.Pointer[Graph]
}

// NewMerger seeds an empty, non-nil Graph so the first merge cycle has a
// "last known good" value to fall back to, and so Snapshot never returns nil.
func NewMerger() *Merger {
	m := &Merger{}
	m.current.Store(&Graph{
		Sources:  map[string]SourceHealth{},
		Checksum: checksum(0, nil, nil, nil, nil),
	})
	return m
}

// Snapshot returns the current Graph. Callers must take this once and read
// every field off the local copy - calling Snapshot again partway through a
// handler reintroduces the exact tearing the pointer swap was meant to
// prevent, just one Load() call later.
func (m *Merger) Snapshot() *Graph {
	return m.current.Load()
}

// Run polls every source on `interval` until ctx is cancelled. Each cycle
// builds a complete new Graph in local variables, then publishes it with a
// single atomic store.
func (m *Merger) Run(ctx context.Context, interval time.Duration) {
	var gen uint64
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			gen++
			prev := m.current.Load()
			next := mergeOnce(ctx, prev, gen)
			m.current.Store(next)
		}
	}
}

func (m *Merger) handleGraph(w http.ResponseWriter, r *http.Request) {
	g := m.Snapshot() // one Load(), reused for every field below
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(g); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func main() {
	merger := NewMerger()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go merger.Run(ctx, 500*time.Millisecond)

	mux := http.NewServeMux()
	mux.HandleFunc("/graph", merger.handleGraph)

	log.Println("serving merged graph on :8080/graph")
	if err := http.ListenAndServe(":8080", mux); err != nil {
		log.Fatal(err)
	}
}
```

```go
// graph_test.go
package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// TestNoTornSnapshot hammers one Merger with overlapping writers and
// concurrent readers, both direct via Snapshot() and over HTTP. Every
// observed Graph must pass Verify(): a Graph assembled from two different
// merge cycles could not satisfy its own checksum, so this catches tearing
// without any external log of what was published to compare against.
func TestNoTornSnapshot(t *testing.T) {
	merger := NewMerger()

	srv := httptest.NewServer(http.HandlerFunc(merger.handleGraph))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	var wg sync.WaitGroup

	// Writers: overlapping merge cycles, deliberately worse than the ticker.
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			var gen uint64
			for {
				select {
				case <-ctx.Done():
					return
				default:
				}
				gen++
				prev := merger.Snapshot()
				next := mergeOnce(ctx, prev, gen*100+uint64(id))
				merger.current.Store(next)
			}
		}(i)
	}

	// Readers: direct.
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-ctx.Done():
					return
				default:
				}
				g := merger.Snapshot()
				if !g.Verify() {
					t.Errorf("torn snapshot observed: generation %d failed checksum verification", g.Generation)
					return
				}
			}
		}()
	}

	// Readers: over HTTP, to exercise the encode-under-swap path too.
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			client := srv.Client()
			for {
				select {
				case <-ctx.Done():
					return
				default:
				}
				resp, err := client.Get(srv.URL)
				if err != nil {
					continue
				}
				var g Graph
				err = json.NewDecoder(resp.Body).Decode(&g)
				resp.Body.Close()
				if err != nil {
					continue
				}
				if !g.Verify() {
					t.Errorf("torn snapshot observed over HTTP: generation %d failed checksum verification", g.Generation)
					return
				}
			}
		}()
	}

	wg.Wait()
}
```

```bash
go mod tidy
go run .                 # serves the merged graph on :8080/graph, rebuilt every 500ms
go test -race -v ./...   # PASS: TestNoTornSnapshot
```

`go run .` rebuilds the graph every 500ms from four simulated sources, each taking
20-220ms and failing roughly 8% of the time. `go test -race ./...` drives eight
concurrent writers issuing overlapping merges against one `Merger` for two seconds,
alongside twenty-four concurrent readers - direct and over HTTP - each calling
`Graph.Verify()` on every read. The test reports `PASS` regardless of how hard you
hammer it, because `atomic.Pointer[Graph]` plus build-then-publish make a torn read
structurally unreachable, not merely unlikely under the load this happens to
generate.

## Conclusion

**Consistency of a combined read is a property you design for explicitly.**
Individually correct synchronization on each piece of state does not compose
into a correct joined view, no matter how finely or coarsely you lock - the
mutex-per-field version above is textbook-correct locking and still wrong.

**The race detector proves the absence of unsynchronized memory access, not the
absence of logical incoherence.** A mutex-protected field-by-field update sails
through `go test -race` while still serving nonsense; a shared map written from
several goroutines fails it immediately even when every write targets a
different key. Both are real classes of bug, and only one of them is what the
tool is built to find - which is why the invariant needs its own test, checked
against the actual read model rather than against internals.

**The shape generalises well beyond a graph of infrastructure state.** Publish a
complete new value or nothing, never a partial one: the same pattern applies to
feature-flag snapshots, config reload, composite health checks, or any read
model assembled by joining several independently updated sources on a timer.

**The costs are real, not hidden.** During the swap window the process holds two
full snapshots in memory instead of one. Readers normally see data up to one
poll interval old, and a degraded source's contribution can be older than that -
bounded by how long it stays unreachable, not by the poll interval. Both are
usually a good trade against ever serving an incoherent mix of old and new, but
neither is free, and pretending otherwise is how the next inconsistency slips
back in.

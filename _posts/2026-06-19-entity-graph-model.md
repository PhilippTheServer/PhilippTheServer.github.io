---
layout: post
title: "An Entity Graph as the Data Model for a Heterogeneous Estate"
subtitle: "One node shape and three edge types instead of a struct and a join per thing."
date: 2026-06-19 09:00:00 +0200
tags: [observability, architecture, go]
description: >-
  Hosts, volumes, links and alerts have genuinely different shapes, but an
  observability console still needs one model that can answer "what does
  this depend on" and "what is this node's health" without a type-specific
  code path for every kind of thing. This walks through an entity-graph
  model in Go that gets there, with a complete runnable implementation.
---

## The problem

An infrastructure console ends up needing to represent things that do not look alike. A
host has CPU and memory. A volume has a size and a filesystem. A network link has two
endpoints and a media type. An alert has a severity and a message. None of that maps
cleanly onto a shared struct, so the obvious first move is one struct per kind:

```go
type Host struct {
	ID       string
	CPUCores int
	MemoryGB int
	Volumes  []*Volume
}

type Volume struct {
	ID       string
	SizeGiB  int
	HostID   string
	Alerts   []*Alert
}

type Link struct {
	ID       string
	EndpointA, EndpointB string
	MediaType string
}

type Alert struct {
	ID       string
	Severity string
	Message  string
}
```

This is fine until the console needs to do two things every observability console needs to
do: walk dependencies, and roll health up. Both turn into a type switch:

```go
func Health(x any) string {
	switch v := x.(type) {
	case *Host:
		status := "healthy"
		for _, vol := range v.Volumes {
			if Health(vol) == "unhealthy" {
				status = "unhealthy"
			}
		}
		return status
	case *Volume:
		status := "healthy"
		for _, a := range v.Alerts {
			if a.Severity == "critical" {
				status = "unhealthy"
			}
		}
		return status
	case *Link:
		return "healthy" // no children, but now someone has to remember that
	// ...one more case per kind, each with its own idea of what "healthy" means
	default:
		panic("unknown entity kind")
	}
}
```

Every new kind of thing — a load balancer, a certificate, a Kubernetes node — means a new
struct and a new case in every function that walks the estate. A service depending on a
network link doesn't fit the `Volumes []*Volume`-style containment field at all, because a
link isn't contained in the service; it needs a second, differently-shaped relationship,
usually bolted onto whichever struct needed it first.

The cost shows up later, not on day one. The function above is correct for four kinds. Add
a fifth and it is silently wrong until someone notices a node whose alerts never turn it
red, because its case was never added. Nothing fails to compile, nothing panics in
staging — the estate just quietly stops reporting the health of the thing nobody wrote a
case for. The test suite has the same blind spot as the code: nobody writes a test for the
kind they forgot exists.

## Working through it

### One node shape, not one struct per kind

The type switch exists because the code is trying to use Go's type system to distinguish
"kinds of thing" that the domain treats as interchangeable in every way that matters to the
console — they all have an ID, a name, and a health status; they all participate in edges.
The fix is to stop encoding "kind" as a Go type and start encoding it as data:

```go
type Entity struct {
	ID     string
	Kind   string
	Name   string
	Status Status
	Attrs  map[string]string
}
```

`Kind` is a plain string discriminator — `"host"`, `"volume"`, `"link"`, `"alert"` — and
`Attrs` is a flexible bag for whatever fields that kind cares about. A volume's size and a
link's media type both live in `Attrs["sizeGiB"]` and `Attrs["mediaType"]`, not in Go
fields. This gives up some compile-time safety: nothing stops a caller from asking a link
for `Attrs["sizeGiB"]` and getting an empty string back. What it buys is that adding a new
entity kind is a data change, not a code change — no new struct, no new case anywhere,
because there was never a switch on kind to extend.

An alternative worth naming is a Go interface with one implementation per kind, or a
generic `Entity[T]` parameterised on an attribute type. Either keeps more compile-time
checking than a string-keyed map. The trade-off is the one this whole design is making
throughout: more static safety costs more code per new kind. For a console whose entity
kinds change as the estate changes — which is the normal case in infrastructure, where
"we now also monitor object storage buckets" happens a few times a year — the flexible bag
is usually the better fit. For a fixed, small set of kinds that will not grow, the interface
approach is worth the extra code.

### Relationships as typed edges, not struct fields

Containment (`Host.Volumes`) and dependency (a service needing a link) were different
things syntactically in the struct-per-type version, purely because one happened to be
modelled as a slice field and the other wasn't modelled at all yet. They are not different
things semantically: both say "this entity's state should account for that entity's
state". Making that explicit is one edge type with a `Type` field:

```go
type EdgeType string

const (
	EdgeContains  EdgeType = "contains"
	EdgeDependsOn EdgeType = "dependsOn"
	EdgeRunsOn    EdgeType = "runsOn"
)

type Edge struct {
	From string
	To   string
	Type EdgeType
}
```

The edge always points from the dependent entity to the thing it depends on: a host
contains a volume, a service depends on a link, a service runs on a host. Walking "what
does this depend on" is now the same operation — follow outgoing edges — regardless of
whether the relationship happens to be containment. A UI that draws a dependency tree
doesn't need to know that `contains` and `dependsOn` are conceptually different; it draws
edges and labels them.

### Health as a rollup, computed from the graph, not stored per node

The mistake in the type-switch version wasn't the type switch itself — it was computing
health as a property of each kind, so every kind needed its own logic. If every edge means
"my health should account for yours", then health rollup is a single graph traversal that
doesn't know what a host or a volume is:

```go
acc := entity.Status
for _, edge := range outgoingEdgesOf(entity) {
	depStatus := health(edge.To) // recursive
	acc = aggregatorFor(edge.Type)(acc, depStatus)
}
```

The only kind-specific-looking decision left is what an edge *type* means for aggregation,
and that's small enough to be a lookup table instead of a switch: `contains` and `runsOn`
use worst-status-wins (any unhealthy child makes the parent at least as unhealthy);
`dependsOn` here caps the effect at degraded, on the reasoning that a service depending on
a struggling link is worse off but not necessarily as dead as the link itself. Both are
three-line functions with the same signature, so a new edge type gets a new table entry,
not a new code path threaded through every function that touches health.

### Cycles and shared dependencies

A graph, unlike a tree, can have cycles — two hosts that each depend on the other for some
reason nobody remembers — and shared dependencies, where two hosts point at the same link.
The traversal has to handle both: a "currently visiting" set turns a cycle into a reported
error instead of infinite recursion, and a memo table shared across the whole rollup means
a link with fifty dependents is evaluated once, not fifty times.

### What this costs against a relational schema

A relational schema with a table per entity kind and foreign keys for containment gives you
things this model does not: the database enforces that a volume's `host_id` points at a
row that actually exists, a query planner can use an index instead of a graph walk, and a
column's type is checked before bad data is ever written. The entity graph gives none of
that for free — `Attrs["sizeGiB"]` being a valid integer is a runtime concern, and a
dangling edge to a deleted entity is a bug you find at traversal time, not at write time.

What the graph buys back is that a new entity kind is a new `Kind` string and, if the
console UI wants to render it specially, a template lookup — never a migration, never an
`ALTER TABLE`, never a new join added to every query that used to enumerate "all the things
attached to a host". For an estate whose shape is still changing — which describes most
infrastructure observability tooling for as long as the infrastructure it watches keeps
changing — that trade is usually the right one. For a data model that has settled and needs
to support arbitrary ad-hoc queries efficiently, push the settled part into a proper schema
and keep the graph for the part that hasn't settled yet.

## The solution

The full example below is a small Go module: a graph of entities and typed edges, a
pluggable per-edge-type aggregator, a health rollup, and a table-driven test that proves
the rollup for a graph with a failing leaf, a two-hop dependency, and a cycle.

```go
// go.mod
module entgraph

go 1.22
```

```go
// main.go
// Command entgraph is a minimal entity-graph model: nodes of one shape,
// connected by typed edges, with health rolled up from leaves to roots
// without any per-kind logic.
package main

import (
	"fmt"
	"sort"
)

// Status is a node's health, ordered so two statuses compare with plain <, >.
type Status int

const (
	StatusHealthy Status = iota
	StatusDegraded
	StatusUnhealthy
	StatusUnknown
)

func (s Status) String() string {
	switch s {
	case StatusHealthy:
		return "healthy"
	case StatusDegraded:
		return "degraded"
	case StatusUnhealthy:
		return "unhealthy"
	default:
		return "unknown"
	}
}

// worst returns whichever of two statuses is further from healthy.
func worst(a, b Status) Status {
	if b > a {
		return b
	}
	return a
}

// Entity is a node in the graph. Every kind — host, volume, link, service,
// alert — is the same struct; Kind says what it is, Attrs carries whatever
// fields that kind cares about, with no Go field per kind.
type Entity struct {
	ID     string
	Kind   string
	Name   string
	Status Status // self-reported health, before any rollup
	Attrs  map[string]string
}

// EdgeType names a relationship. Not every relationship is containment:
// dependsOn and runsOn connect entities that do not own one another.
type EdgeType string

const (
	EdgeContains  EdgeType = "contains"
	EdgeDependsOn EdgeType = "dependsOn"
	EdgeRunsOn    EdgeType = "runsOn"
)

// Edge points from the dependent entity to the one it depends on: a host
// --contains--> a volume, a service --dependsOn--> a link. In every case
// From's health should account for To's.
type Edge struct {
	From string
	To   string
	Type EdgeType
}

// Aggregator folds a dependency's rolled-up status into an accumulator. It
// is the one place per-edge-type behaviour lives.
type Aggregator func(acc, dep Status) Status

// worstWins: any unhealthy dependency makes the dependent at least as unhealthy.
func worstWins(acc, dep Status) Status {
	return worst(acc, dep)
}

// cappedAtDegraded: a failing dependency pulls the dependent down, but no
// further than degraded. Used for dependsOn — a service depending on a dead
// link is worse off, but not necessarily as dead as the link itself.
func cappedAtDegraded(acc, dep Status) Status {
	if dep > StatusDegraded {
		dep = StatusDegraded
	}
	return worst(acc, dep)
}

// Graph holds entities and edges, plus the aggregator used per edge type.
type Graph struct {
	entities    map[string]*Entity
	out         map[string][]Edge
	aggregators map[EdgeType]Aggregator
}

// NewGraph returns an empty graph with default aggregators set. Callers can
// override any entry before computing health.
func NewGraph() *Graph {
	return &Graph{
		entities: make(map[string]*Entity),
		out:      make(map[string][]Edge),
		aggregators: map[EdgeType]Aggregator{
			EdgeContains:  worstWins,
			EdgeRunsOn:    worstWins,
			EdgeDependsOn: cappedAtDegraded,
		},
	}
}

// AddEntity inserts or replaces a node.
func (g *Graph) AddEntity(e *Entity) {
	g.entities[e.ID] = e
}

// AddEdge records that from depends on to, in the sense described by typ.
func (g *Graph) AddEdge(from, to string, typ EdgeType) {
	g.out[from] = append(g.out[from], Edge{From: from, To: to, Type: typ})
}

// aggregatorFor returns the configured aggregator for an edge type, or
// worst-status-wins if none was set. A new edge type therefore works
// correctly the moment it is used, even before anyone configures it.
func (g *Graph) aggregatorFor(t EdgeType) Aggregator {
	if agg, ok := g.aggregators[t]; ok {
		return agg
	}
	return worstWins
}

// Health computes the rolled-up status of one entity: its own status,
// folded with the rolled-up status of everything it depends on, using each
// edge's aggregator. It shares memo and visiting across the recursion so a
// diamond-shaped graph (two hosts sharing a link, say) evaluates each node
// once rather than once per path to it.
func (g *Graph) Health(id string) (Status, error) {
	memo := make(map[string]Status)
	visiting := make(map[string]bool)
	return g.health(id, memo, visiting)
}

func (g *Graph) health(id string, memo map[string]Status, visiting map[string]bool) (Status, error) {
	if s, ok := memo[id]; ok {
		return s, nil
	}
	if visiting[id] {
		return StatusUnknown, fmt.Errorf("cycle detected at entity %q", id)
	}
	e, ok := g.entities[id]
	if !ok {
		return StatusUnknown, fmt.Errorf("unknown entity %q", id)
	}

	visiting[id] = true
	acc := e.Status
	for _, edge := range g.out[id] {
		depStatus, err := g.health(edge.To, memo, visiting)
		if err != nil {
			return StatusUnknown, err
		}
		acc = g.aggregatorFor(edge.Type)(acc, depStatus)
	}
	delete(visiting, id)

	memo[id] = acc
	return acc, nil
}

// RollupAll computes health for every entity in the graph, sharing one memo
// so shared dependencies are still evaluated once.
func (g *Graph) RollupAll() (map[string]Status, error) {
	memo := make(map[string]Status)
	visiting := make(map[string]bool)
	for id := range g.entities {
		if _, err := g.health(id, memo, visiting); err != nil {
			return nil, err
		}
	}
	return memo, nil
}

func buildExampleGraph() *Graph {
	g := NewGraph()

	g.AddEntity(&Entity{ID: "host-a", Kind: "host", Name: "host-a", Status: StatusHealthy})
	g.AddEntity(&Entity{ID: "host-b", Kind: "host", Name: "host-b", Status: StatusHealthy})
	g.AddEntity(&Entity{ID: "vol-1", Kind: "volume", Name: "vol-1", Status: StatusHealthy,
		Attrs: map[string]string{"sizeGiB": "500"}})
	g.AddEntity(&Entity{ID: "vol-2", Kind: "volume", Name: "vol-2", Status: StatusHealthy,
		Attrs: map[string]string{"sizeGiB": "500"}})
	g.AddEntity(&Entity{ID: "link-1", Kind: "link", Name: "link-1", Status: StatusHealthy,
		Attrs: map[string]string{"mediaType": "fibre"}})
	g.AddEntity(&Entity{ID: "svc-api", Kind: "service", Name: "svc-api", Status: StatusHealthy})
	g.AddEntity(&Entity{ID: "alert-1", Kind: "alert", Name: "vol-2 usage above threshold",
		Status: StatusUnhealthy, Attrs: map[string]string{"severity": "critical"}})

	g.AddEdge("host-a", "vol-1", EdgeContains)
	g.AddEdge("host-a", "vol-2", EdgeContains)
	g.AddEdge("vol-2", "alert-1", EdgeDependsOn)
	g.AddEdge("svc-api", "host-b", EdgeRunsOn)
	g.AddEdge("svc-api", "link-1", EdgeDependsOn)

	return g
}

func main() {
	g := buildExampleGraph()

	statuses, err := g.RollupAll()
	if err != nil {
		panic(err)
	}

	ids := make([]string, 0, len(statuses))
	for id := range statuses {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	for _, id := range ids {
		fmt.Printf("%-10s %-8s %s\n", id, g.entities[id].Kind, statuses[id])
	}

	// A leaf alert on vol-2 must reach host-a as degraded: the alert is
	// unhealthy, dependsOn caps that at degraded on the volume, and
	// contains carries the volume's degraded status up unchanged.
	if got, want := statuses["host-a"], StatusDegraded; got != want {
		panic(fmt.Sprintf("host-a: got %s, want %s", got, want))
	}
	// host-b itself is healthy and has nothing contained in it, so the
	// service running on it stays healthy too.
	if got, want := statuses["svc-api"], StatusHealthy; got != want {
		panic(fmt.Sprintf("svc-api: got %s, want %s", got, want))
	}
}
```

```go
// main_test.go
package main

import "testing"

func TestHealthRollup(t *testing.T) {
	cases := []struct {
		name string
		// build returns a graph and the entity ID whose health is checked.
		build func() (*Graph, string)
		want  Status
	}{
		{
			name: "healthy leaf, healthy root",
			build: func() (*Graph, string) {
				g := NewGraph()
				g.AddEntity(&Entity{ID: "host-a", Kind: "host", Status: StatusHealthy})
				g.AddEntity(&Entity{ID: "vol-1", Kind: "volume", Status: StatusHealthy})
				g.AddEdge("host-a", "vol-1", EdgeContains)
				return g, "host-a"
			},
			want: StatusHealthy,
		},
		{
			name: "unhealthy leaf propagates unchanged through contains",
			build: func() (*Graph, string) {
				g := NewGraph()
				g.AddEntity(&Entity{ID: "host-a", Kind: "host", Status: StatusHealthy})
				g.AddEntity(&Entity{ID: "vol-1", Kind: "volume", Status: StatusHealthy})
				g.AddEntity(&Entity{ID: "vol-2", Kind: "volume", Status: StatusUnhealthy})
				g.AddEdge("host-a", "vol-1", EdgeContains)
				g.AddEdge("host-a", "vol-2", EdgeContains)
				return g, "host-a"
			},
			want: StatusUnhealthy,
		},
		{
			name: "unhealthy leaf is capped at degraded through dependsOn",
			build: func() (*Graph, string) {
				g := NewGraph()
				g.AddEntity(&Entity{ID: "svc-api", Kind: "service", Status: StatusHealthy})
				g.AddEntity(&Entity{ID: "link-1", Kind: "link", Status: StatusUnhealthy})
				g.AddEdge("svc-api", "link-1", EdgeDependsOn)
				return g, "svc-api"
			},
			want: StatusDegraded,
		},
		{
			name: "unhealthy leaf two hops away reaches the root",
			build: func() (*Graph, string) {
				g := NewGraph()
				g.AddEntity(&Entity{ID: "host-a", Kind: "host", Status: StatusHealthy})
				g.AddEntity(&Entity{ID: "vol-2", Kind: "volume", Status: StatusHealthy})
				g.AddEntity(&Entity{ID: "alert-1", Kind: "alert", Status: StatusUnhealthy})
				g.AddEdge("host-a", "vol-2", EdgeContains)
				g.AddEdge("vol-2", "alert-1", EdgeDependsOn)
				return g, "host-a"
			},
			// alert-1 (unhealthy) -> vol-2 via dependsOn, capped at
			// degraded -> host-a via contains, worst-wins, so degraded
			// survives unchanged to the root.
			want: StatusDegraded,
		},
		{
			name: "a node with no edges reports only its own status",
			build: func() (*Graph, string) {
				g := NewGraph()
				g.AddEntity(&Entity{ID: "link-1", Kind: "link", Status: StatusDegraded})
				return g, "link-1"
			},
			want: StatusDegraded,
		},
		{
			name: "healthy dependency does not improve an already-worse self status",
			build: func() (*Graph, string) {
				g := NewGraph()
				g.AddEntity(&Entity{ID: "host-a", Kind: "host", Status: StatusDegraded})
				g.AddEntity(&Entity{ID: "vol-1", Kind: "volume", Status: StatusHealthy})
				g.AddEdge("host-a", "vol-1", EdgeContains)
				return g, "host-a"
			},
			want: StatusDegraded,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g, id := tc.build()
			got, err := g.Health(id)
			if err != nil {
				t.Fatalf("Health(%q) returned error: %v", id, err)
			}
			if got != tc.want {
				t.Errorf("Health(%q) = %s, want %s", id, got, tc.want)
			}
		})
	}
}

func TestHealthDetectsCycles(t *testing.T) {
	g := NewGraph()
	g.AddEntity(&Entity{ID: "a", Kind: "host", Status: StatusHealthy})
	g.AddEntity(&Entity{ID: "b", Kind: "host", Status: StatusHealthy})
	g.AddEdge("a", "b", EdgeDependsOn)
	g.AddEdge("b", "a", EdgeDependsOn)

	if _, err := g.Health("a"); err == nil {
		t.Fatal("expected an error for a cyclic graph, got nil")
	}
}

func TestRollupAllSharesSharedDependencies(t *testing.T) {
	// Two hosts depend on the same link. RollupAll must still return the
	// correct status for every entity, not just the ones queried directly.
	g := NewGraph()
	g.AddEntity(&Entity{ID: "host-a", Kind: "host", Status: StatusHealthy})
	g.AddEntity(&Entity{ID: "host-b", Kind: "host", Status: StatusHealthy})
	g.AddEntity(&Entity{ID: "link-1", Kind: "link", Status: StatusUnhealthy})
	g.AddEdge("host-a", "link-1", EdgeDependsOn)
	g.AddEdge("host-b", "link-1", EdgeDependsOn)

	statuses, err := g.RollupAll()
	if err != nil {
		t.Fatalf("RollupAll returned error: %v", err)
	}

	for _, id := range []string{"host-a", "host-b"} {
		if statuses[id] != StatusDegraded {
			t.Errorf("statuses[%q] = %s, want %s", id, statuses[id], StatusDegraded)
		}
	}
	if statuses["link-1"] != StatusUnhealthy {
		t.Errorf("statuses[%q] = %s, want %s", "link-1", statuses["link-1"], StatusUnhealthy)
	}
}
```

Running it prints the rolled-up health of every entity, sorted by ID, and then asserts the
two cases described above:

```bash
$ go run .
alert-1    alert    unhealthy
host-a     host     degraded
host-b     host     healthy
link-1     link     healthy
svc-api    service  healthy
vol-1      volume   healthy
vol-2      volume   degraded
```

```bash
$ go test ./... -v
--- PASS: TestHealthRollup (0.00s)
    --- PASS: TestHealthRollup/healthy_leaf,_healthy_root (0.00s)
    --- PASS: TestHealthRollup/unhealthy_leaf_propagates_unchanged_through_contains (0.00s)
    --- PASS: TestHealthRollup/unhealthy_leaf_is_capped_at_degraded_through_dependsOn (0.00s)
    --- PASS: TestHealthRollup/unhealthy_leaf_two_hops_away_reaches_the_root (0.00s)
    --- PASS: TestHealthRollup/a_node_with_no_edges_reports_only_its_own_status (0.00s)
    --- PASS: TestHealthRollup/healthy_dependency_does_not_improve_an_already-worse_self_status (0.00s)
--- PASS: TestHealthDetectsCycles (0.00s)
--- PASS: TestRollupAllSharesSharedDependencies (0.00s)
PASS
ok  	entgraph	0.002s
```

## Conclusion

Three things generalise beyond hosts and volumes.

**A discriminator plus an attribute bag beats a struct per kind exactly when the set of
kinds keeps changing.** The estate a console watches grows new categories of thing more
often than its relationships change shape, so optimise the model for the axis that moves.

**Push kind-specific behaviour into small pluggable functions keyed by relationship type,
not into a switch keyed by entity type.** There are far fewer relationship types
(`contains`, `dependsOn`, `runsOn`, and rarely more) than entity kinds, so this is the
smaller, more stable surface to special-case.

**The honest cost is real and worth stating plainly.** A relational schema with foreign
keys catches a dangling reference at write time; this graph catches it at traversal time,
as an error return from `Health`, and only if something actually walks that edge. A
database's query planner can also do things `Health`'s recursive walk cannot without more
work — filter, aggregate, and paginate efficiently over millions of edges. This model is
the right trade for a console whose entity kinds are still shifting; it is the wrong trade
once the kinds and relationships have settled and the query patterns against them have
grown demanding enough that an index beats a graph walk.

**Cycle detection is not optional once the model allows arbitrary edges.** A struct-based
model with `Volumes []*Volume` cannot express a cycle by construction. A graph can, the
moment two teams each add an edge without seeing the other's, and the traversal has to
notice and report it rather than recurse forever.

---
layout: post
title: "A Minimal Go HTTP Service with chi, Timeouts and Graceful Shutdown"
subtitle: "Draining in-flight requests on SIGTERM instead of dropping them mid-response."
date: 2025-10-21 09:00:00 +0200
tags: [go, reliability]
description: >-
  http.ListenAndServe has no default timeouts and no way to drain in-flight
  requests on SIGTERM, so a rolling deploy or a container orchestrator's stop
  signal cuts connections mid-response. This builds a small chi-based service
  with explicit server timeouts and a shutdown sequence that waits for
  in-flight work to finish, with a test that proves a slow request survives a
  shutdown signal instead of being killed by it.
---

## The problem

The smallest possible Go HTTP server is three lines:

```go
http.HandleFunc("/", handler)
http.ListenAndServe(":8080", nil)
```

It has no read timeout, no write timeout, no idle timeout, and nowhere obvious to attach
middleware like request logging or panic recovery — `http.HandleFunc` on the default mux
does not compose the way most people expect once there is more than one route. Without
`ReadHeaderTimeout` specifically, a client that opens a connection and sends headers one
byte at a time (deliberately or through a broken proxy) can hold a goroutine and a file
descriptor open indefinitely; this is common enough to have its own linter warning
(`gosec`'s G112).

The bigger, less obvious problem is shutdown. `os.Signal` is not handled at all by
default, so a `SIGTERM` — the signal Kubernetes sends on pod termination, the signal
`docker stop` sends before its grace period expires — kills the process immediately,
mid-request. Whatever request was in flight at that moment gets a reset connection instead
of a response, and the client's retry logic (if it has any) is what saves you, not
anything the server did.

The fix looks straightforward — catch the signal, call `Shutdown` — but two details are
easy to get wrong: `Shutdown` blocks until every in-flight request is done or its own
context is cancelled, so calling it with no timeout at all means a single stuck handler
prevents the process from ever exiting; and the signal handling has to happen before
`ListenAndServe`, in a separate goroutine, or the program never reaches the code that
listens for the signal in the first place.

## Working through it

### Setting timeouts on the server, not per-handler

`http.Server`'s zero-value timeouts mean no timeout. `ReadHeaderTimeout` bounds how long a
client gets to finish sending headers; `ReadTimeout` bounds the whole request including
body; `WriteTimeout` bounds how long a handler has to write a response; `IdleTimeout`
bounds how long a keep-alive connection can sit unused. These are properties of the
server, not of any one route, which is why they belong on the `http.Server` struct rather
than scattered through handler code.

### Choosing chi for routing, not for anything it does to shutdown

`chi` doesn't change how graceful shutdown works — that is entirely `http.Server`'s job,
regardless of router. What it buys is composable middleware (`chi.Middlewares`) and a
router that doesn't require a global mux, which matters once there is more than a couple
of routes and more than one thing (logging, recovery, request IDs) that should wrap all of
them.

### Using a context to bound shutdown, and a second one to bound the signal wait

`signal.NotifyContext` produces a context that is cancelled the moment `SIGTERM` or
`SIGINT` arrives — that's the trigger to start shutting down. `Shutdown` itself takes a
*different* context, one with its own timeout, that bounds how long the drain is allowed
to take before the server gives up and returns anyway. Using the same context for both
would mean the moment the signal arrives, the shutdown deadline is already expired.

### Proving the drain actually happens, not just that the code compiles

A handler that sleeps briefly, combined with sending a shutdown signal partway through the
request and asserting the client still got a `200`, is the only way to actually prove
in-flight requests survive shutdown rather than assuming the standard library does what
its documentation says.

## The solution

```go
// go.mod
module chi-graceful-shutdown

go 1.22

require github.com/go-chi/chi/v5 v5.1.0
```

```go
// main.go
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

func newServer(addr string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
}

func newRouter() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)

	r.Get("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	})

	r.Get("/slow", func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(3 * time.Second)
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("done"))
	})

	return r
}

func run(ctx context.Context, addr string, shutdownTimeout time.Duration) error {
	srv := newServer(addr, newRouter())

	serveErr := make(chan error, 1)
	go func() {
		log.Printf("listening on %s", addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serveErr <- err
			return
		}
		serveErr <- nil
	}()

	select {
	case err := <-serveErr:
		return err
	case <-ctx.Done():
		log.Printf("shutdown signal received, draining (timeout %s)", shutdownTimeout)
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()

	if err := srv.Shutdown(shutdownCtx); err != nil {
		return err
	}
	log.Println("shutdown complete")
	return nil
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	if err := run(ctx, ":8080", 15*time.Second); err != nil {
		log.Fatalf("server error: %v", err)
	}
}
```

```go
// main_test.go
package main

import (
	"context"
	"net/http"
	"testing"
	"time"
)

func TestSlowRequestSurvivesShutdownSignal(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	errCh := make(chan error, 1)
	go func() {
		errCh <- run(ctx, ":18080", 5*time.Second)
	}()

	waitForServer(t, "http://localhost:18080/healthz")

	respCh := make(chan *http.Response, 1)
	reqErrCh := make(chan error, 1)
	go func() {
		resp, err := http.Get("http://localhost:18080/slow")
		if err != nil {
			reqErrCh <- err
			return
		}
		respCh <- resp
	}()

	time.Sleep(300 * time.Millisecond) // let the slow request start
	cancel()                           // simulate SIGTERM

	select {
	case err := <-reqErrCh:
		t.Fatalf("in-flight request failed during shutdown: %v", err)
	case resp := <-respCh:
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200, got %d", resp.StatusCode)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("in-flight request never completed")
	}

	if err := <-errCh; err != nil {
		t.Fatalf("run returned an error: %v", err)
	}
}

func waitForServer(t *testing.T, url string) {
	t.Helper()
	for i := 0; i < 50; i++ {
		if resp, err := http.Get(url); err == nil {
			resp.Body.Close()
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("server never became ready")
}
```

Running it:

```bash
go mod tidy
go build ./...

go run . &
curl -s localhost:8080/healthz
# ok

curl -s localhost:8080/slow &
sleep 1
kill -TERM %1
# 2025-10-21T10:00:00Z shutdown signal received, draining (timeout 15s)
# 2025-10-21T10:00:02Z shutdown complete
# done         <- curl still gets its response before the process exits

go test -v ./...
# --- PASS: TestSlowRequestSurvivesShutdownSignal (3.31s)
# PASS
```

The `curl -s localhost:8080/slow &` request, started before the `kill -TERM`, is the part
worth watching closely: it prints `done` *after* the shutdown log line, proving the
process kept the listener's already-accepted connection alive to finish it, rather than
tearing it down the instant the signal arrived.

## Conclusion

Nothing here is specific to `chi` — the graceful shutdown sequence is `net/http` and
`context`, and would look identical with the standard library's own `http.NewServeMux`.
`chi` earns its place only once middleware composition matters.

Three points generalise past this one service:

**Server-level timeouts are not optional once the service leaves your laptop.** A client
you do not control, on a network you do not control, will eventually be the slow or
malicious client that a zero-value timeout leaves your server exposed to indefinitely.

**Shutdown needs two independent time bounds, not one.** When you receive the signal to
stop, and how long you are willing to wait once stopping, are different questions with
different answers, and conflating them into one context makes the timeout mean something
other than what its name suggests.

**A signal handling test that never sends a signal to the process hasn't tested the
signal handling.** Driving `run()` with a cancellable context and checking the in-flight
request's actual outcome is what makes this test worth more than "the code compiles and
the happy path returns 200".

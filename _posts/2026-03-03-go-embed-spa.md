---
layout: post
title: "Embedding a Built Frontend Into a Go Binary with go:embed"
subtitle: "One process and one version number instead of a static server paired with an API"
date: 2026-03-03 09:00:00 +0200
tags: [go, frontend, architecture]
description: >-
  Serving a single-page application from a separate static file server next to its API
  is two processes that have to be deployed and versioned together but usually are not.
  This article embeds a built frontend directly into the Go binary that also serves the
  API, with a complete example including the single-page-application routing fallback
  that this approach needs to get right.
---

## The problem

A typical small application ends up as two deployables: a static file server (nginx, or
a CDN) hosting the built frontend, and a Go binary serving the API. They are developed
in one repository and released as two artefacts, which means two version numbers that
are supposed to move together and, in practice, drift:

```
frontend  v1.4.0  →  deployed to the CDN
backend   v1.3.0  →  still rolling out
```

Nothing enforces that these match. A frontend release that shipped ahead of the API it
calls is a live incident that looks like a backend bug — new UI code calling an endpoint
or field the currently-deployed API does not have yet — and it is caused entirely by a
deployment ordering problem, not application logic. Rolling back is two rollbacks, and
if only one of them completes you have created a second mismatch on the way to fixing
the first.

For an application small enough that it does not need independent scaling of frontend
and backend — most internal tools, most single-team products — this is two moving parts
bought for no benefit. Go's `embed` package, since Go 1.16, can compile a directory of
files directly into the binary and expose it as an `fs.FS`. Ship one binary, and the
frontend it serves is, by construction, the frontend that shipped with that exact build.

## Working through it

### What go:embed actually does

`//go:embed dist` is a compiler directive: at build time, the compiler reads everything
under `dist/` and stores it inside the resulting binary, exposed to your code as an
`embed.FS`. This happens at *compile* time, not at runtime, which has one direct
consequence worth stating plainly: the frontend has to already be built — `npm run
build` or equivalent has already produced `dist/` — before `go build` runs. Embedding
does not build the frontend; it packages the output of a build step that already
happened.

### Serving it like a file server, except for one detail

`http.FileServer` over the embedded filesystem handles real assets — `/app.js`,
`/style.css` — correctly out of the box. A single-page application's client-side router
also needs paths that are not real files at all, such as `/settings` or `/users/42`,
to resolve to `index.html` so the JavaScript router can take over and render the right
view. Handing an unknown path straight to `http.FileServer` returns a 404 instead,
because as far as the file server is concerned, no such file exists — which is correct,
and also not what a single-page application needs.

The fix is a small wrapper: try to serve the path as a real file first, and only fall
back to `index.html` when it does not exist *and* it does not look like a request for a
static asset (so a genuinely missing `.js` file still 404s instead of silently returning
HTML).

### Keeping the API and the static handler from fighting over paths

Mounting both under one `http.ServeMux` needs an explicit boundary — API routes under a
prefix such as `/api/`, everything else falling through to the SPA handler — otherwise a
frontend route that happens to share a path segment with an API route becomes ambiguous
about which handler should win.

## The solution

A complete, minimal frontend (standing in for whatever a real build tool would produce),
a Go server that embeds it, serves an API alongside it, and a test proving the SPA
fallback and the API both work from the one binary.

```
.
├── dist/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── go.mod
├── main.go
└── main_test.go
```

```html
<!-- dist/index.html — stands in for a real build tool's output -->
<!doctype html>
<html>
<head><title>go:embed demo</title><link rel="stylesheet" href="/style.css"></head>
<body>
  <div id="app">loading…</div>
  <script src="/app.js"></script>
</body>
</html>
```

```javascript
// dist/app.js
fetch("/api/hello")
  .then((r) => r.json())
  .then((data) => {
    document.getElementById("app").textContent = data.message;
  });
```

```css
/* dist/style.css */
body { font-family: sans-serif; margin: 2rem; }
```

```go
// main.go
package main

import (
	"embed"
	"encoding/json"
	"io/fs"
	"log"
	"net/http"
	"path"
	"strings"
)

//go:embed dist
var embeddedFrontend embed.FS

func spaHandler(distFS fs.FS) http.Handler {
	fileServer := http.FileServer(http.FS(distFS))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cleaned := path.Clean(strings.TrimPrefix(r.URL.Path, "/"))
		if cleaned == "." {
			cleaned = "index.html"
		}
		if _, err := fs.Stat(distFS, cleaned); err != nil {
			// Not a real file. If it looks like one (has an extension), a genuine
			// 404 is correct. Otherwise it's a client-side route: serve the app shell.
			if path.Ext(cleaned) == "" {
				r.URL.Path = "/"
			} else {
				w.WriteHeader(http.StatusNotFound)
				return
			}
		}
		fileServer.ServeHTTP(w, r)
	})
}

func apiHelloHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"message": "hello from the same binary that served this page",
	})
}

func newServer() (http.Handler, error) {
	distFS, err := fs.Sub(embeddedFrontend, "dist")
	if err != nil {
		return nil, err
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/api/hello", apiHelloHandler)
	mux.Handle("/", spaHandler(distFS))
	return mux, nil
}

func main() {
	handler, err := newServer()
	if err != nil {
		log.Fatal(err)
	}
	log.Println("listening on :8080")
	log.Fatal(http.ListenAndServe(":8080", handler))
}
```

```go
// main_test.go
package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestStaticAssetIsServedDirectly(t *testing.T) {
	handler, err := newServer()
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodGet, "/style.css", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "font-family") {
		t.Fatalf("expected the real stylesheet, got: %s", rec.Body.String())
	}
}

func TestUnknownClientRouteFallsBackToIndex(t *testing.T) {
	handler, err := newServer()
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodGet, "/settings/profile", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), `id="app"`) {
		t.Fatalf("expected the SPA shell, got: %s", rec.Body.String())
	}
}

func TestMissingAssetStill404s(t *testing.T) {
	handler, err := newServer()
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodGet, "/does-not-exist.js", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", rec.Code)
	}
}

func TestAPIRouteIsNotSwallowedBySPAHandler(t *testing.T) {
	handler, err := newServer()
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodGet, "/api/hello", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "hello from the same binary") {
		t.Fatalf("unexpected body: %s", rec.Body.String())
	}
}
```

```
go.mod
```
```
module embed-spa-demo

go 1.22
```

```bash
go test ./...
go build -o server .
./server &
curl -s localhost:8080/api/hello
curl -s localhost:8080/settings/profile | grep 'id="app"'
```

```
ok      embed-spa-demo  0.004s
{"message":"hello from the same binary that served this page"}
<div id="app">loading…</div>
```

## Conclusion

**Embedding does not remove a build step, it removes a deployment coordination
problem.** The frontend still has to be built before `go build` runs; what disappears is
the second release pipeline, the second version number, and the window where they can
disagree.

**A single-page application's routing is a genuine special case, not an edge case to
skip.** Any embedded-static-file setup that forwards straight to `http.FileServer` will
work for every asset and fail for every client-side route, and that failure is easy not
to notice until someone refreshes the browser on a page other than the root.

**This trades away independent scaling and independent deployment, and that trade is not
free for every application.** A frontend and backend that genuinely need to scale or
release on different schedules should stay separate; the win here is specific to
applications small enough that "one binary, one version" is a simplification rather than
a constraint you will regret.

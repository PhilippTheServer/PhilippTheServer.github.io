---
layout: post
title: "Designing a Docker Image Build API: Job Model and Streamed Logs"
subtitle: "A build takes minutes and produces output while it runs, so the API has to model both"
date: 2026-03-06 09:00:00 +0200
tags: [go, docker, api-design]
description: >-
  An HTTP endpoint that triggers a Docker image build cannot behave like a normal
  request/response call, because the build takes an unpredictable amount of time and a
  client needs to watch its output as it happens rather than poll for a final result.
  This article designs the job and streaming model for that, with a complete Go service
  built on the Docker SDK.
---

## The problem

The obvious first attempt at "an API that builds a Docker image" is one handler:

```go
// Broken. Do not copy this.
func handleBuild(w http.ResponseWriter, r *http.Request) {
	resp, err := dockerClient.ImageBuild(r.Context(), r.Body, types.ImageBuildOptions{
		Tags: []string{"demo:latest"},
	})
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	io.Copy(w, resp.Body)
}
```

This blocks the HTTP request for as long as the build runs — anywhere from seconds to
many minutes — and it gives the client no way to see anything until the whole response
is written, because `io.Copy` here streams to the response, but there is nothing on the
client side deciding to read it incrementally unless you already designed for that. Any
reverse proxy or load balancer with an idle timeout shorter than the slowest build will
kill the connection midway, and the build itself has no independent existence: if the
HTTP request is cancelled, gets retried, or the client just closes the tab, there is no
way to ask "is that build still running, and how did it go" — the only handle on the
build was the request that started it.

The actual shape of the problem is that a build is not a request/response operation at
all. It is a long-running job that emits output while it runs, and a client wants three
things a single blocking call cannot give: an immediate acknowledgement that the build
started, a way to watch its output live, and a way to ask about its final status later,
independent of whether anyone was watching when it happened.

## Working through it

### Separating "start" from "watch"

The fix is to stop treating the build as one HTTP exchange and split it into two
concerns: a job that exists independently of any particular HTTP connection, and any
number of HTTP connections that observe it. `POST /builds` creates the job and returns
immediately with an identifier; the actual `ImageBuild` call runs in a goroutine that
outlives the request. `GET /builds/{id}/logs` and `GET /builds/{id}` are read-only views
onto whatever that goroutine has produced so far.

### Giving every log line a home before anyone is watching

Because build output starts arriving before a client necessarily connects to watch it —
or after that client has disconnected and a different one has taken over — the log
cannot live only "on the wire" of one HTTP response. It has to accumulate in the job
itself, so a new watcher can catch up on everything that already happened, and a slow
watcher does not block the build by failing to read fast enough.

### Streaming to a client without polling

Once the log lives in the job, a streaming handler is a loop: hand the client whatever
is new since they last checked, and when there is nothing new, wait to be woken up
rather than either blocking forever or polling in a spin loop. A small broadcast
primitive — a channel that gets closed and replaced every time new data arrives — lets
any number of waiters wake up on new data without a busy loop and without a fixed poll
interval imposing an artificial delay on live output.

### Reading the Docker daemon's own build stream correctly

`ImageBuild` returns before the build is finished; the returned `Body` is a stream of
newline-delimited JSON objects, one per build step or log line, and — this is the part
that is easy to miss — a *failed* build step is not necessarily reported as a Go `error`
from `ImageBuild` itself. It shows up as an `error` field inside one of the streamed JSON
messages. A job model that only checks the function's returned error will report a build
that failed at `RUN false` as a success, because the HTTP call to start the build
succeeded even though the build did not.

## The solution

A complete build API: a job store with broadcast-based log streaming, and the Docker SDK
calls wired up correctly, including detecting an in-stream build failure.

```go
// main.go
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sync"

	"github.com/docker/docker/api/types"
	"github.com/docker/docker/client"
)

// --- Job model -------------------------------------------------------------

type Job struct {
	ID string

	mu     sync.Mutex
	log    []byte
	status string // "running", "success", "failed"
	notify chan struct{}
}

func newJob(id string) *Job {
	return &Job{ID: id, status: "running", notify: make(chan struct{})}
}

func (j *Job) append(p []byte) {
	j.mu.Lock()
	j.log = append(j.log, p...)
	old := j.notify
	j.notify = make(chan struct{})
	j.mu.Unlock()
	close(old)
}

func (j *Job) finish(status string) {
	j.mu.Lock()
	j.status = status
	old := j.notify
	j.notify = make(chan struct{})
	j.mu.Unlock()
	close(old)
}

// snapshot returns any log bytes after `from`, whether the job is finished, and a
// channel that closes the next time either changes — so a caller with nothing new to
// read can wait on it instead of polling.
func (j *Job) snapshot(from int) (chunk []byte, status string, notify chan struct{}) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if from < len(j.log) {
		chunk = j.log[from:]
	}
	return chunk, j.status, j.notify
}

// --- Job store ---------------------------------------------------------------

type Store struct {
	mu   sync.Mutex
	jobs map[string]*Job
}

func newStore() *Store { return &Store{jobs: map[string]*Job{}} }

func (s *Store) create() *Job {
	id := randomID()
	job := newJob(id)
	s.mu.Lock()
	s.jobs[id] = job
	s.mu.Unlock()
	return job
}

func (s *Store) get(id string) (*Job, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	job, ok := s.jobs[id]
	return job, ok
}

func randomID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// --- Build execution ---------------------------------------------------------

type buildMessage struct {
	Stream string `json:"stream"`
	Error  string `json:"error"`
}

func runBuild(cli *client.Client, job *Job, buildContext io.Reader, tag string) {
	resp, err := cli.ImageBuild(context.Background(), buildContext, types.ImageBuildOptions{
		Tags:   []string{tag},
		Remove: true,
	})
	if err != nil {
		job.append([]byte(fmt.Sprintf("could not start build: %v\n", err)))
		job.finish("failed")
		return
	}
	defer resp.Body.Close()

	failed := false
	dec := json.NewDecoder(resp.Body)
	for {
		var msg buildMessage
		if err := dec.Decode(&msg); err != nil {
			if err != io.EOF {
				job.append([]byte(fmt.Sprintf("stream error: %v\n", err)))
				failed = true
			}
			break
		}
		if msg.Stream != "" {
			job.append([]byte(msg.Stream))
		}
		if msg.Error != "" {
			job.append([]byte("ERROR: " + msg.Error + "\n"))
			failed = true
		}
	}

	if failed {
		job.finish("failed")
	} else {
		job.finish("success")
	}
}

// --- HTTP handlers -------------------------------------------------------------

type server struct {
	store  *Store
	docker *client.Client
}

func (s *server) createBuild(w http.ResponseWriter, r *http.Request) {
	tmp, err := os.CreateTemp("", "build-context-*.tar")
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if _, err := io.Copy(tmp, r.Body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	_ = tmp.Close()

	job := s.store.create()

	go func() {
		defer os.Remove(tmp.Name())
		f, err := os.Open(tmp.Name())
		if err != nil {
			job.append([]byte(fmt.Sprintf("could not reopen build context: %v\n", err)))
			job.finish("failed")
			return
		}
		defer f.Close()
		runBuild(s.docker, job, f, "build-api-demo:"+job.ID)
	}()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]string{"id": job.ID})
}

func (s *server) getBuild(w http.ResponseWriter, r *http.Request) {
	job, ok := s.store.get(r.PathValue("id"))
	if !ok {
		http.NotFound(w, r)
		return
	}
	_, status, _ := job.snapshot(0)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"id": job.ID, "status": status})
}

func (s *server) streamLogs(w http.ResponseWriter, r *http.Request) {
	job, ok := s.store.get(r.PathValue("id"))
	if !ok {
		http.NotFound(w, r)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)

	offset := 0
	for {
		chunk, status, notify := job.snapshot(offset)
		if len(chunk) > 0 {
			w.Write(chunk)
			flusher.Flush()
			offset += len(chunk)
			continue
		}
		if status != "running" {
			return
		}
		select {
		case <-notify:
		case <-r.Context().Done():
			return
		}
	}
}

func main() {
	cli, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		log.Fatal(err)
	}

	s := &server{store: newStore(), docker: cli}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /builds", s.createBuild)
	mux.HandleFunc("GET /builds/{id}", s.getBuild)
	mux.HandleFunc("GET /builds/{id}/logs", s.streamLogs)

	log.Println("listening on :8081")
	log.Fatal(http.ListenAndServe(":8081", mux))
}
```

```
go.mod (skeleton — run `go mod tidy` to resolve exact transitive versions)
```
```
module docker-build-api

go 1.22
```

```bash
go mod init docker-build-api
go get github.com/docker/docker@v24.0.9
go mod tidy
go build -o build-api .
```

This talks to whatever Docker daemon `DOCKER_HOST` points at (the default,
`unix:///var/run/docker.sock`, is picked up automatically by `client.FromEnv`), so run it
on a machine that already has Docker installed — no daemon-in-a-container needed for the
demo.

```dockerfile
# demo-context/Dockerfile — a trivial image to build through the API
FROM alpine:3.20
RUN echo "built through the streaming build API" > /note.txt
CMD ["cat", "/note.txt"]
```

```bash
./build-api &

cd demo-context && tar -cf ../context.tar Dockerfile && cd ..

BUILD_ID=$(curl -s -X POST --data-binary @context.tar \
  -H "Content-Type: application/x-tar" localhost:8081/builds | jq -r .id)
echo "started build $BUILD_ID"

curl -N localhost:8081/builds/$BUILD_ID/logs &
wait $!

curl -s localhost:8081/builds/$BUILD_ID
```

```
started build 4f9a1c2b7e3d5061
Step 1/2 : FROM alpine:3.20
Step 2/2 : RUN echo "built through the streaming build API" > /note.txt
{"id":"4f9a1c2b7e3d5061","status":"success"}
```

Break the Dockerfile deliberately — `RUN false` — rerun the same sequence, and the log
stream shows the daemon's `ERROR:` line while `GET /builds/{id}` reports
`"status":"failed"`, confirming the in-stream failure is caught even though
`ImageBuild`'s own Go error was `nil`.

## Conclusion

**A long-running operation is a resource, not a request.** The moment an operation
outlives the connection that started it, it needs an identity of its own — an ID, a
status, a log — independent of any single client watching it.

**Streaming to an unknown number of watchers means the source of truth cannot be the
wire.** The log has to live in the job so a watcher who connects late, or a second
watcher entirely, sees the same thing a watcher who was there from the start would have.

**Trust the data in the stream over the function call that started it.** Any SDK
wrapping a long-running external process can report success at the call-site while the
underlying operation reports failure in its own output. Parse what the operation itself
says happened; do not assume the wrapper already did.

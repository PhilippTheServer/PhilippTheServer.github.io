---
layout: post
title: "Traefik's 60-Second readTimeout Covers the Whole Request Body"
subtitle: "Every slow upload to Nextcloud died at 60 seconds, a few hundred kilobytes per second short of its Content-Length. The data volume got three terabytes bigger first, which was a fine idea that fixed nothing."
date: 2026-09-23 09:00:00 +0200
tags: [kubernetes, networking, reliability, testing]
description: >-
  "Expected filesize of 104857600 bytes but read 29491200": Traefik's readTimeout
  defaults to 60s and bounds the whole request body, so slow uploads get cut.
---

## The problem

The company Nextcloud runs on the Kubernetes cluster, behind the cluster's Traefik,
behind a second Traefik at the public edge that terminates TLS. Its ingress manifest
carries a comment promising that the 16 GB upload limit is honoured end to end. One
morning every upload through the browser failed, and `nextcloud.log` recorded, for each
of the four parallel chunks the web client sends:

```text
PUT /remote.php/dav/uploads/.../web-file-upload-<id>/{1,2,3,4}  -> 400
"Expected filesize of 104857600 bytes but read (from Nextcloud client) and wrote
 (to Nextcloud storage) 29491200 bytes."
```

A 100 MiB chunk arrives as 29.5 MB. The server wrote what it received, compared it to
the `Content-Length` the client announced, and rightly refused to call that a file.

The first suspect was the disk, and the disk had a genuine case to answer: the data
volume sat at 97 %, 963 GB of 1000. It is a static CephFS subvolume, which a PVC cannot
resize, so it was grown to 3000 GiB through its CephFS quota the same day. Uploads
kept failing. A disk at 33 % does not truncate writes out of spite. The volume change
stayed, because it was needed anyway. It was simply a fix for next quarter's incident.

PHP was the second suspect and was acquitted quickly: `max_execution_time=0`,
`max_input_time=-1`, and `post_max_size` and `upload_max_filesize` both at 16 GB.
Nothing in Nextcloud's stack was configured to give up.

## Working through it

The log lines carry the clue if you read the timestamps and not just the numbers. All
four chunks aborted in the **same second**, at **different** byte counts: 29.4 MB,
29.5 MB, 31.9 MB. A size limit cuts every stream at the same byte. A full disk fails
whichever write comes next. Four streams dying together at different offsets is a
clock: something gave each request a fixed amount of time, and each got as far as its
bandwidth allowed.

The bandwidth agrees. At roughly 0.5 MB/s per parallel stream, 60 seconds buys about
30 MB, which is what arrived. A 100 MiB chunk at that rate needs over 200 seconds.

Two hops could own that clock. Holding an idle connection open against each one
separated them: the cluster Traefik's `web` entrypoint closed the socket after 61
seconds, and the edge Traefik was still holding an equivalent connection at 210
seconds when the test stopped. So the edge was innocent, and the cutter was the
Traefik inside the cluster.

Its Helm values set no `transport` block on any entrypoint, so every timeout was the
default. Traefik's default for `respondingTimeouts.readTimeout` is 60 seconds, and the
documentation says precisely what it bounds: the maximum duration for reading the
*entire request, including the body*. Many people read "read timeout" as "time to
send the headers", or "time between two packets". Traefik means the whole upload.

The default was not always 60 seconds. It used to be zero, meaning no limit, and
changed in **v2.11.2**. A patch release. The change is a reasonable defence against
slowloris clients on an internet-facing entrypoint, and the migration notes do mention
it. Nobody reads migration notes for a patch release, which is what makes a patch
release such an effective place to put one.

The failure is easy to reproduce with public images. A twenty-line backend reports
how much of the body it actually received:

```python
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Upload(BaseHTTPRequestHandler):
    def do_PUT(self):
        expected = int(self.headers["Content-Length"])
        got = 0
        while got < expected:
            chunk = self.rfile.read(min(65536, expected - got))
            if not chunk:
                break
            got += len(chunk)
        status = 201 if got == expected else 400
        body = f"expected {expected} bytes, read {got}\n".encode()
        print(body.decode(), end="", flush=True)
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("", 8000), Upload).serve_forever()
```

Traefik sits in front of it with a file provider and a configurable `readTimeout`,
turned down to five seconds so the demonstration does not need a coffee break:

```yaml
# compose.yml
services:
  traefik:
    image: traefik:v3.5.6
    command:
      - --providers.file.filename=/dynamic.yml
      - --entrypoints.web.address=:80
      - --entrypoints.web.transport.respondingTimeouts.readTimeout=${READ_TIMEOUT:-60s}
    ports: ["8080:80"]
    volumes:
      - ./dynamic.yml:/dynamic.yml:ro

  upload:
    image: python:3.13-alpine
    command: python -u /backend.py
    volumes:
      - ./backend.py:/backend.py:ro
```

```yaml
# dynamic.yml
http:
  routers:
    upload:
      rule: PathPrefix(`/`)
      entryPoints: [web]
      service: upload
  services:
    upload:
      loadBalancer:
        servers:
          - url: http://upload:8000
```

A 1 MB body at 100 KB/s needs about ten seconds:

```bash
head -c 1000000 /dev/urandom > chunk.bin

READ_TIMEOUT=5s docker compose up -d
curl -s -T chunk.bin --limit-rate 100k -w 'HTTP %{http_code}\n' http://localhost:8080/chunk
docker compose logs upload --no-log-prefix | grep expected
docker compose down
```

```text
Client Closed RequestHTTP 499
expected 1000000 bytes, read 524181
```

Half the body, then silence. The backend sees a short PUT, which is exactly the shape
of Nextcloud's complaint. And the client is told `499 Client Closed Request`, which
is a generous account of events from the component that closed it.

With `READ_TIMEOUT=0` the same upload takes its ten seconds and finishes:

```text
expected 1000000 bytes, read 1000000
HTTP 201
```

## The solution

The fix is in the Traefik Helm values, on both HTTP entrypoints: `web`, which takes
traffic from the edge, and `websecure`, which mesh clients use directly:

```yaml
ports:
  web:
    # Traefik defaults respondingTimeouts.readTimeout to 60s, and it bounds reading the
    # *whole* request, body included. 0 = no limit.
    transport:
      respondingTimeouts:
        readTimeout: 0
  websecure:
    transport:
      respondingTimeouts:
        readTimeout: 0
```

Zero, not a larger number. Any finite value is a claim about the slowest client that
will ever upload the largest file, and the ingress promises 16 GB. At a few hundred
KB/s a single 16 GB PUT runs for hours, and choosing "three hours" only moves the day
this article gets written again.

Removing the limit here is safe because of where these entrypoints sit, not because
slowloris stopped being a thing. They are never reachable from the internet. Their
only clients are the edge proxy and peers on the NetBird mesh. The edge is the public
trust boundary, and slow-client defence belongs there, where the untrusted clients
are. A timeout on an internal hop protects nobody. It only turns a slow upload into a
truncated one.

The rejected alternative was shrinking Nextcloud's chunk size until each chunk fits in
60 seconds. That would have fixed the browser on a good connection, left slower
clients and single-PUT WebDAV uploads broken, and left the same trap set for every
other application behind this Traefik. It treats the symptom in one tenant and keeps
the bug for everyone else.

The regression check reads the values file and fails if either entrypoint regains a
finite budget:

```python
"""Traefik's respondingTimeouts.readTimeout bounds reading the whole request, body
included. Any finite value silently truncates a slow upload mid-body, so the
entrypoints that carry app traffic must pin it to 0."""

from pathlib import Path

import yaml

VALUES = Path(__file__).resolve().parent.parent / "platform/traefik/values.yaml"
HTTP_ENTRYPOINTS = ("web", "websecure")


def test_http_entrypoints_never_time_out_reading_a_request_body():
    ports = yaml.safe_load(VALUES.read_text())["ports"]
    for name in HTTP_ENTRYPOINTS:
        timeouts = ports[name]["transport"]["respondingTimeouts"]
        assert timeouts["readTimeout"] == 0, (
            f"entrypoint {name}: readTimeout must be 0, a finite budget cuts slow uploads"
        )
```

The comment in the Nextcloud ingress that promised 16 GB end to end now names the
Traefik setting that promise depends on. It had been false since this Traefik was
deployed. Nobody noticed, because nobody had uploaded anything slow enough.

## Conclusion

A timeout's name describes what it bounds much less reliably than its documentation
does. "Read timeout" suggests headers, or idle gaps between packets. In Traefik it is
the wall-clock budget for the entire request, and a request body is the one part of
HTTP whose duration the server cannot predict.

The diagnosis came from the shape of the failure, not from any one number. Same
second, different byte counts, across parallel streams, means time and not size. That
pattern points past storage, past PHP, and past every limit expressed in bytes,
straight at something with a clock. Measuring each hop with an idle socket then
names the hop.

The expensive part was the order of suspicion. The disk looked guilty because it was
nearly full, and "nearly full" is a plausible story for any write failure. It got
three terabytes before anyone checked that the timestamps lined up. That was not
wasted: the volume needed growing anyway, and the Nextcloud users now have room for a
great many more files named `Screenshot (47).png`.

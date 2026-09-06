---
layout: post
title: "Alerting on Symptoms Instead of Metrics: Designing Alerts People Do Not Ignore"
subtitle: "Monitoring that tells you a user is unhappy, instead of that a number moved."
date: 2026-03-31 09:00:00 +0200
tags: [observability, alerting, reliability, testing]
description: >-
  Most monitoring failures are not missing data, they are the wrong alert on
  good data, and the class of outage nobody catches is the one where every
  metric stays green. This works through the difference between alerting on a
  resource number and alerting on what a user experiences, and includes a
  runnable Prometheus and blackbox_exporter stack plus a tested DNS check
  that catches a failure no metric-based alert can see.
---

## The problem

The first monitoring system I built alerted on CPU. It worked exactly as designed and was
almost entirely useless.

High CPU is not a problem. It is sometimes a *sign* of a problem, and it is sometimes a
machine doing its job well. Alerting on it taught everyone the worst lesson a monitoring
system can teach: that alerts are things you glance at and dismiss. Once a team learns
that, the system is effectively dead, and the one alert that eventually fires correctly
gets dismissed along with everything else.

The harder version of this problem is not noisy alerts, it is a failure that produces no
alert at all because nothing being measured is actually broken. A service can report
itself perfectly healthy — CPU normal, memory normal, process running — while being
completely unreachable, because every layer between the process and the user is invisible
to the process itself. That gap is easy to miss in a design review, because a dashboard
full of green tiles looks exactly like success.

## Working through it

### Alert on what the user experiences, graph everything else

This is the rule that fixed my first system: **alert on what the user experiences, graph
everything else.** If a page is slow, alert. If a queue stops draining, alert. If a
certificate expires in three days, alert. If CPU sits at 90%, put it on a dashboard and
leave everyone alone until they are already investigating something else that fired.

The test that separates the two: *if this fires and I do nothing, will anybody notice?* If
the honest answer is no, it is a graph, not an alert. The uncomfortable corollary: an alert
that has fired ten times and been ignored ten times is not a monitoring gap, it is a
monitoring *bug*. Leaving it in place is choosing to train people to ignore alerts.

### Black-box and white-box answer different questions

White-box monitoring reads a system's own account of itself — exported metrics, queue
depths, internal error counters. It answers *why*. Black-box monitoring is an outside
observer doing what a user does: fetch the URL, resolve the name, complete the handshake.
It answers *whether*. Neither replaces the other, and the mistake is assuming white-box
monitoring is sufficient on its own — a service cannot report a failure in a layer it
cannot see, and DNS, the load balancer, certificates and firewall rules all sit in front of
it.

### The failure where every metric is green

Here is a shape worth internalising, because it generalises well past the mechanism that
produces it.

A DNS wildcard synthesises an answer for a name only if nothing exists beneath that name.
Put any record — any type at all — under a name, and the name now *exists*, and the
wildcard is forbidden from answering for it. The name then resolves with a perfectly
successful response code (`NOERROR`) and an empty answer section. No error. No failure.
Just no address.

The practical consequence: a leftover record from something that was supposed to clean up
after itself can take a hostname off the internet, silently, while every neighbouring name
served by the same wildcard keeps working. Now consider what your monitoring says while
this is happening: the service is up, its metrics are green, its certificates are valid.
Every dashboard is fine, because the failure sits in the resolution step that happens
*before* anyone reaches the service, and nothing you are measuring inside the service can
see that step.

This is a category, not a one-off: **failures where every component reports success and
the composition still does not work.** A certificate chain that validates piece by piece
and not as a chain. A load balancer routing happily to an empty backend pool. The only
thing that catches these is an outside observer checking the actual composed outcome, not
each component's opinion of itself — and for DNS specifically, checking that a query
*returned an address*, not merely that it succeeded.

### What a good alert contains, and what cardinality does to the budget

An alert is a message to a tired person, and it should be written for them: what is broken
in user terms, how you know, and where to look. `ProbeFailureRateHigh` on a service nobody
remembers deploying tells a person at 3am nothing worth having woken up for.

Separately, the other way monitoring dies is by becoming too expensive to run, almost
always through labels. Every distinct combination of label values is a separate time
series; put a user ID or a request path in a label and cost grows with traffic in a way
nobody intended. The rule: a label is for something you would *group by*. If you would
never write a query grouping on it, it is a log field, not a label.

## The solution

A small Prometheus stack demonstrates the actual distinction: alerting on a symptom
(`probe_success`, from an outside observer) rather than a resource metric.

```yaml
# docker-compose.yml
services:
  web:
    image: nginx:1.27-alpine

  blackbox:
    image: prom/blackbox-exporter:v0.25.0
    volumes:
      - ./blackbox.yml:/etc/blackbox_exporter/config.yml:ro
    command: ["--config.file=/etc/blackbox_exporter/config.yml"]

  prometheus:
    image: prom/prometheus:v2.55.1
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./rules.yml:/etc/prometheus/rules.yml:ro
    command: ["--config.file=/etc/prometheus/prometheus.yml"]
    ports:
      - "9090:9090"
    depends_on: [blackbox, web]
```

```yaml
# blackbox.yml
modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_status_codes: [200]
```

```yaml
# prometheus.yml
global:
  scrape_interval: 5s
  evaluation_interval: 5s
rule_files:
  - rules.yml
scrape_configs:
  - job_name: blackbox
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets: ["http://web:80"]
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox:9115
```

{% raw %}
```yaml
# rules.yml
groups:
  - name: symptom-alerts
    rules:
      # Anti-example, not wired to fire: alerts on a resource number, which
      # is sometimes a problem and often a machine doing its job.
      # - alert: HighCPU
      #   expr: 100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 90

      - alert: WebsiteDown
        expr: probe_success{job="blackbox"} == 0
        for: 15s
        labels:
          severity: page
        annotations:
          summary: "web is not answering HTTP requests"
          description: >-
            The blackbox probe against {{ $labels.instance }} has failed for
            15 seconds straight. A user hitting this endpoint right now gets
            nothing back.
```
{% endraw %}

```bash
docker compose up -d
sleep 5
curl -s 'http://localhost:9090/api/v1/query?query=probe_success' | jq '.data.result[0].value[1]'
# "1"  -- the site answers, nothing pending

docker compose stop web
sleep 20
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[].labels.alertname'
# "WebsiteDown"
```

That is the whole difference in one alert: it fires because a real request failed, not
because a resource crossed an arbitrary line.

The DNS trap needs its own check, because "the query succeeded" and "the query returned an
address" are different facts, and only one of them means anything to a user:

```python
#!/usr/bin/env python3
# check_dns_answers.py
"""A resolution 'succeeding' is not the same as returning an address.
NOERROR with an empty answer section happens whenever a wildcard is
shadowed by an unrelated record placed under the name it would otherwise
answer for, and it looks like success to anything that only checks the
response code."""
import sys
import dns.resolver


def has_address(hostname: str, resolver: dns.resolver.Resolver | None = None) -> bool:
    resolver = resolver or dns.resolver.Resolver()
    try:
        answer = resolver.resolve(hostname, "A")
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        return False  # NOERROR, empty answer section: this is the trap
    return len(answer) > 0


def main() -> int:
    hostname = sys.argv[1]
    if has_address(hostname):
        print(f"OK: {hostname} resolves to an address")
        return 0
    print(f"CRITICAL: {hostname} returned no address (NXDOMAIN or empty answer)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# test_check_dns_answers.py
from unittest.mock import MagicMock

import dns.resolver

from check_dns_answers import has_address


def test_empty_answer_section_counts_as_failure():
    resolver = MagicMock()
    resolver.resolve.side_effect = dns.resolver.NoAnswer()
    assert has_address("shadowed.example.com", resolver) is False


def test_nxdomain_counts_as_failure():
    resolver = MagicMock()
    resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
    assert has_address("does-not-exist.example.com", resolver) is False


def test_real_answer_counts_as_success():
    resolver = MagicMock()
    resolver.resolve.return_value = ["203.0.113.10"]
    assert has_address("example.com", resolver) is True
```

```bash
pip install "dnspython==2.7.0" pytest
pytest test_check_dns_answers.py
# 3 passed
```

The first test is the point of the whole article: a resolver call that raised no error and
returned no failure code still has to be treated as down, because that is exactly the shape
of the outage a metrics-only alert cannot see.

## Conclusion

**Alert on the composed outcome, not on any single component's opinion of itself.** A
service, a certificate, and a load balancer can each report success while the path between
a user and the answer is broken. Only an external check of the actual outcome — an address
came back, a page loaded, a queue drained — catches that class of failure.

**An alert that nobody acts on is not neutral, it is actively harmful.** It spends down the
trust that the next, real alert depends on. Delete it or fix it; do not leave it as
evidence that the system is thorough.

**Good monitoring is mostly deletion.** The instinct after an incident is to add an alert
for it, and two years later there are three hundred, most of which have never once been
useful. Twelve alerts everyone trusts catch fewer things than three hundred, and they catch
them at 3am, when someone is actually reading.

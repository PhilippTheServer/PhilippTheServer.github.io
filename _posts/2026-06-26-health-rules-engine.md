---
layout: post
title: "A Rules File for Health Evaluation, Validated at Load Time"
subtitle: "Moving thresholds out of code and into a file that fails at startup, not three weeks later."
date: 2026-06-26 09:00:00 +0200
tags: [observability, go]
description: >-
  Hardcoding a threshold per metric works until you have more than a handful of
  them, and a hand-edited rules file that is only checked when a rule fires
  will eventually ship a typo straight into production. Here is a small Go
  health-evaluation engine that validates its entire rules file before it
  evaluates a single metric.
---

## The problem

Every small monitoring tool starts the same way: a handful of `if` statements checking
whether a metric crossed a threshold.

```go
if cpuUsage > 90 {
    alert("CPU usage high")
}
if diskFree < 10 {
    alert("Disk space low")
}
```

It works, and it works for a surprisingly long time. Then someone adds a fourth
environment, a second team starts asking for a threshold change without a code review,
and you end up wanting the thresholds in a file instead of in a binary. So you write a
YAML rules file and a loader, and the loader looks like this:

```go
// Broken. Do not copy this.
for _, rule := range rules {
    metricValue := metrics[rule.Metric]
    if rule.Operator == "gt" && metricValue > rule.Threshold {
        fire(rule)
    }
}
```

This compiles, runs, and passes every test you write against valid input. The failure
shows up later, when someone adds a rule with `operator: greter` — a typo — or a rule
referencing a metric name that does not exist yet, or a threshold written as the string
`"90"` instead of the number `90`. None of that fails at load time. The loader parses the
YAML happily, because YAML does not know what an operator is supposed to be, and the bad
rule sits there silently until the one moment it is supposed to fire — at which point it
either does nothing, or panics inside whatever is evaluating metrics for every other rule
too.

That is the shape of the problem: a rules file is configuration, and configuration errors
should be startup errors. A monitoring rule that cannot possibly work should never reach
the evaluation loop.

## Working through it

### Separate the shape from the meaning

A rules file has two kinds of correctness. The first is structural: is this valid YAML,
does every field have the right type. Go's `yaml.Unmarshal` (via `gopkg.in/yaml.v3`)
already gives you that for free, as long as your struct tags are strict and you check the
error it returns.

The second kind is semantic, and YAML cannot check it: is this operator one we support, is
this metric name spelled the way the collector spells it, does this threshold make sense
for this operator (a `gt` threshold of `0` on a percentage that never goes below `0` is
legal YAML and a rule that will never usefully fire). Semantic correctness needs code you
write on purpose, run once, at load time.

### Validate the whole file before evaluating any rule

The mistake in the naive loader is validating a rule the first time it is evaluated,
implicitly, by whether the comparison happens to work. Instead, walk every rule once at
load time and check it against an explicit contract: known operator, known metric,
threshold of the right type, no duplicate rule names. Collect every failure rather than
stopping at the first one — a rules file with three typos should report three errors in
one run, not one error per restart.

```go
func Validate(rules []Rule, knownMetrics map[string]bool) error {
    var errs []string
    seen := map[string]bool{}

    for i, r := range rules {
        if r.Name == "" {
            errs = append(errs, fmt.Sprintf("rule %d: name is required", i))
            continue
        }
        if seen[r.Name] {
            errs = append(errs, fmt.Sprintf("rule %q: duplicate name", r.Name))
        }
        seen[r.Name] = true

        if !knownMetrics[r.Metric] {
            errs = append(errs, fmt.Sprintf("rule %q: unknown metric %q", r.Name, r.Metric))
        }
        if _, ok := operators[r.Operator]; !ok {
            errs = append(errs, fmt.Sprintf("rule %q: unknown operator %q", r.Name, r.Operator))
        }
    }

    if len(errs) > 0 {
        return fmt.Errorf("invalid rules file:\n  %s", strings.Join(errs, "\n  "))
    }
    return nil
}
```

The `knownMetrics` set is the piece that makes this genuinely useful rather than a schema
checker with extra steps: it is supplied by whatever collects metrics in your system, so a
rule referencing `disk_free_percent` when the collector only ever emits `disk_free_bytes`
fails immediately, by name, instead of silently evaluating against a zero value forever.

### Make the operator set closed, not stringly typed

`rule.Operator == "gt"` scattered through the evaluator is the same mistake as the
threshold typo, just smaller: a new operator means touching the evaluation loop, and a
misspelled one compiles fine and does nothing. Model operators as a lookup table from
string to function instead, so "is this operator known" and "how does it behave" are
answered in exactly one place, and validation and evaluation both consult it.

```go
var operators = map[string]func(value, threshold float64) bool{
    "gt": func(v, t float64) bool { return v > t },
    "gte": func(v, t float64) bool { return v >= t },
    "lt": func(v, t float64) bool { return v < t },
    "lte": func(v, t float64) bool { return v <= t },
}
```

Validation checks membership in this map. Evaluation calls the function the map already
gave it. There is no third place where the set of valid operators could drift out of sync.

### Test the failure path, not only the success path

The property worth proving is not "valid rules evaluate correctly" — that is the easy,
obvious case that every manual test already covers. It is "an invalid rules file is
rejected before a single metric is evaluated". Write that as an explicit test: load a
rules file with a bad operator, and assert that `Load` returns an error and that no
`Evaluate` call ever happens.

## The solution

The complete program: a `main.go` that loads and validates a rules file at startup, an
evaluation engine, a sample rules file, and a test proving a malformed file fails fast.

```go
// main.go
package main

import (
    "fmt"
    "os"

    "example.com/healthrules/rules"
)

func main() {
    metrics := map[string]float64{
        "cpu_usage_percent":  93.4,
        "disk_free_percent":  6.1,
        "queue_depth":        12,
    }
    known := map[string]bool{}
    for name := range metrics {
        known[name] = true
    }

    r, err := rules.Load("rules.yaml", known)
    if err != nil {
        fmt.Fprintln(os.Stderr, "refusing to start:", err)
        os.Exit(1)
    }

    for _, result := range rules.Evaluate(r, metrics) {
        if result.Fired {
            fmt.Printf("FIRED: %s (value=%.1f %s %.1f)\n",
                result.Rule.Name, result.Value, result.Rule.Operator, result.Rule.Threshold)
        }
    }
}
```

```go
// rules/rules.go
package rules

import (
    "fmt"
    "os"
    "strings"

    "gopkg.in/yaml.v3"
)

type Rule struct {
    Name      string  `yaml:"name"`
    Metric    string  `yaml:"metric"`
    Operator  string  `yaml:"operator"`
    Threshold float64 `yaml:"threshold"`
}

type Result struct {
    Rule  Rule
    Value float64
    Fired bool
}

var operators = map[string]func(value, threshold float64) bool{
    "gt":  func(v, t float64) bool { return v > t },
    "gte": func(v, t float64) bool { return v >= t },
    "lt":  func(v, t float64) bool { return v < t },
    "lte": func(v, t float64) bool { return v <= t },
}

func Parse(data []byte) ([]Rule, error) {
    var doc struct {
        Rules []Rule `yaml:"rules"`
    }
    if err := yaml.Unmarshal(data, &doc); err != nil {
        return nil, fmt.Errorf("parsing rules file: %w", err)
    }
    return doc.Rules, nil
}

func Validate(rules []Rule, knownMetrics map[string]bool) error {
    var errs []string
    seen := map[string]bool{}

    for i, r := range rules {
        if r.Name == "" {
            errs = append(errs, fmt.Sprintf("rule %d: name is required", i))
            continue
        }
        if seen[r.Name] {
            errs = append(errs, fmt.Sprintf("rule %q: duplicate name", r.Name))
        }
        seen[r.Name] = true

        if !knownMetrics[r.Metric] {
            errs = append(errs, fmt.Sprintf("rule %q: unknown metric %q", r.Name, r.Metric))
        }
        if _, ok := operators[r.Operator]; !ok {
            errs = append(errs, fmt.Sprintf("rule %q: unknown operator %q", r.Name, r.Operator))
        }
    }

    if len(errs) > 0 {
        return fmt.Errorf("invalid rules file:\n  %s", strings.Join(errs, "\n  "))
    }
    return nil
}

func Load(path string, knownMetrics map[string]bool) ([]Rule, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, fmt.Errorf("reading rules file: %w", err)
    }
    parsed, err := Parse(data)
    if err != nil {
        return nil, err
    }
    if err := Validate(parsed, knownMetrics); err != nil {
        return nil, err
    }
    return parsed, nil
}

func Evaluate(rules []Rule, metrics map[string]float64) []Result {
    results := make([]Result, 0, len(rules))
    for _, r := range rules {
        value := metrics[r.Metric]
        fired := operators[r.Operator](value, r.Threshold)
        results = append(results, Result{Rule: r, Value: value, Fired: fired})
    }
    return results
}
```

```yaml
# rules.yaml
rules:
  - name: cpu-high
    metric: cpu_usage_percent
    operator: gt
    threshold: 90
  - name: disk-low
    metric: disk_free_percent
    operator: lt
    threshold: 10
  - name: queue-backed-up
    metric: queue_depth
    operator: gte
    threshold: 100
```

{% raw %}
```go
// rules/rules_test.go
package rules

import "testing"

func TestValidateRejectsUnknownOperator(t *testing.T) {
    data := []byte(`
rules:
  - name: bad-rule
    metric: cpu_usage_percent
    operator: greter
    threshold: 90
`)
    parsed, err := Parse(data)
    if err != nil {
        t.Fatalf("Parse should succeed on structurally valid YAML: %v", err)
    }

    known := map[string]bool{"cpu_usage_percent": true}
    err = Validate(parsed, known)
    if err == nil {
        t.Fatal("expected Validate to reject an unknown operator, got nil error")
    }
}

func TestValidateRejectsUnknownMetric(t *testing.T) {
    data := []byte(`
rules:
  - name: bad-rule
    metric: disk_free_percent
    operator: lt
    threshold: 10
`)
    parsed, _ := Parse(data)
    known := map[string]bool{"cpu_usage_percent": true} // disk_free_percent missing

    err := Validate(parsed, known)
    if err == nil {
        t.Fatal("expected Validate to reject an unknown metric, got nil error")
    }
}

func TestLoadNeverEvaluatesAnInvalidFile(t *testing.T) {
    // A file that fails validation must return before any rule can be
    // evaluated. We prove this indirectly: Load returns an error, and there
    // is no result set for Evaluate to have produced.
    tmp := t.TempDir() + "/rules.yaml"
    if err := writeFile(tmp, `
rules:
  - name: broken
    metric: not_a_real_metric
    operator: gt
    threshold: 1
`); err != nil {
        t.Fatal(err)
    }

    _, err := Load(tmp, map[string]bool{"cpu_usage_percent": true})
    if err == nil {
        t.Fatal("expected Load to fail on a rules file referencing an unknown metric")
    }
}

func TestEvaluateFiresOnValidRules(t *testing.T) {
    rules := []Rule{{Name: "cpu-high", Metric: "cpu_usage_percent", Operator: "gt", Threshold: 90}}
    metrics := map[string]float64{"cpu_usage_percent": 95}

    results := Evaluate(rules, metrics)
    if len(results) != 1 || !results[0].Fired {
        t.Fatalf("expected rule to fire, got %+v", results)
    }
}
```
{% endraw %}

```go
// rules/testhelpers_test.go
package rules

import "os"

func writeFile(path, contents string) error {
    return os.WriteFile(path, []byte(contents), 0o644)
}
```

```go
// go.mod
module example.com/healthrules

go 1.22

require gopkg.in/yaml.v3 v3.0.1
```

Running it:

```bash
go mod tidy
go test ./...
go run .
```

Expected test output:

```
ok      example.com/healthrules/rules  0.003s
```

Expected program output against the sample `rules.yaml` and the metrics in `main.go`:

```
FIRED: cpu-high (value=93.4 gt 90.0)
FIRED: disk-low (value=6.1 lt 10.0)
```

`queue-backed-up` does not fire, because `12 >= 100` is false — which is exactly what the
rules file says should happen, and exactly what the test suite would have caught if it
were wrong.

To see the fail-fast behaviour directly, change the sample file's `queue-backed-up`
operator to `gtr` and run `go run .` again:

```
refusing to start: invalid rules file:
  rule "queue-backed-up": unknown operator "gtr"
```

The program exits with status 1 before touching a single metric.

## Conclusion

A rules file is only safer than hardcoded thresholds if it is validated as strictly as
code is compiled. Loading it and hoping the YAML parser catches your mistakes is not
validation — it is trusting the wrong layer to notice the wrong kind of error.

Three points generalise beyond this specific engine:

**Structural validity and semantic validity are different checks and need different code.**
A YAML or JSON schema tells you the shapes are right. It has no opinion on whether
`operator: greter` is a mistake, because as far as the parser is concerned it is a
perfectly good string. Write the semantic check yourself, and run it once, in full, before
anything downstream sees the data.

**A closed set beats a stringly typed one.** Modelling operators as a map from name to
behaviour means "is this a valid operator" and "what does this operator do" can never
disagree, because they are answered by the same lookup.

**The test that matters is the one for the file that should not load.** It is easy to test
that correct configuration behaves correctly — that case was going to work anyway. The
test worth writing is the one that proves a broken rules file is rejected at start-up,
loudly, before it can silently do nothing in production.

#!/usr/bin/env python3
"""Collapse the ad-hoc labels onto a controlled vocabulary.

A tag used once is a note, not a tag: it makes no filter useful and no reader wiser.
The vocabulary below is closed — an article may only carry tags from it, and
scripts/check_site.rb enforces that so it stays closed.
"""
import json, collections, pathlib

VOCAB = [
    # what it runs on
    "linux", "ansible", "infrastructure-as-code", "docker", "kubernetes", "gitops",
    # what it stores in
    "ceph", "storage", "databases", "redis",
    # what it talks over
    "networking", "dns",
    # who is allowed
    "security", "vault", "secrets-management", "identity", "tls",
    # whether it is working
    "observability", "alerting", "testing",
    # what it is written in
    "python", "fastapi", "go", "cpp", "frontend",
    # what it is
    "llm", "agents", "embedded",
    # how it is built
    "api-design", "architecture", "performance", "reliability", "ci-cd",
    "automation", "documentation",
]

MAP = {
    "systemd": "linux", "provisioning": "infrastructure-as-code", "hardening": "security",
    "permissions": "linux", "idempotence": "infrastructure-as-code", "sysctl": "linux",
    "containers": "docker", "docker-compose": "docker", "containerd": "docker",
    "buildkit": "docker", "build-performance": "performance", "multi-arch": "docker",
    "qemu": "docker", "container-registry": "docker", "helm": "kubernetes",
    "argocd": "gitops", "applicationset": "gitops", "app-of-apps": "gitops",
    "crd": "kubernetes", "csi": "kubernetes", "cephfs": "ceph", "crush": "ceph",
    "statefulset": "kubernetes", "kubelet": "kubernetes", "cni": "networking",
    "object-storage": "storage", "s3": "storage", "capacity-planning": "performance",
    "postgresql": "databases", "mysql": "databases", "sqlalchemy": "databases",
    "alembic": "databases", "migrations": "databases", "influxdb": "databases",
    "sqlite": "databases", "sql": "databases", "time-series": "databases",
    "event-sourcing": "redis", "lua": "redis", "wireguard": "networking",
    "vpn": "networking", "netbird": "networking", "zero-trust": "networking",
    "mtu": "networking", "multicast": "networking", "industrial-iot": "embedded",
    "iot": "embedded", "hardware": "embedded", "arm64": "embedded",
    "edge-compute": "embedded", "raspberry-pi": "embedded", "cross-compilation": "cpp",
    "cmake": "cpp", "vcpkg": "cpp", "websocket": "cpp", "keycloak": "identity",
    "oidc": "identity", "sso": "identity", "authentication": "identity",
    "authorization": "identity", "rbac": "identity", "pki": "tls",
    "certificates": "tls", "cert-manager": "tls", "acme": "tls", "encryption": "security",
    "jwt": "identity", "external-secrets": "secrets-management", "kv-store": "vault",
    "sidecar": "vault", "prompt-injection": "security", "sandboxing": "security",
    "permissions-model": "security", "privacy": "security", "data-integrity": "reliability",
    "prometheus": "observability", "metrics": "observability", "monitoring": "observability",
    "blackbox-monitoring": "observability", "service-discovery": "observability",
    "health-checks": "observability", "dead-mans-switch": "alerting",
    "opentelemetry": "observability", "drift-detection": "observability",
    "rules-engine": "observability", "data-visualization": "frontend",
    "graph-visualization": "frontend", "canvas": "frontend", "playwright": "testing",
    "ux": "frontend", "angular": "frontend", "typescript": "frontend", "spa": "frontend",
    "css": "frontend", "design-systems": "frontend", "nginx": "networking",
    "ssr": "frontend", "pwa": "frontend", "web-development": "frontend",
    "pydantic": "fastapi", "validation": "fastapi", "error-handling": "api-design",
    "project-structure": "architecture", "microservices": "architecture",
    "design-patterns": "architecture", "domain-modeling": "architecture",
    "state-machine": "architecture", "data-modeling": "architecture",
    "distributed-systems": "architecture", "patterns": "architecture",
    "concurrency": "performance", "algorithms": "performance", "resilience": "reliability",
    "idempotency": "reliability", "consistency": "reliability", "outbox-pattern": "reliability",
    "graceful-shutdown": "reliability", "process-supervision": "reliability",
    "uv": "python", "ruff": "python", "tooling": "python", "cli": "python",
    "cli-design": "python", "scaffolding": "python", "developer-tools": "python",
    "asyncio": "python", "pure-functions": "testing", "dry-run": "testing",
    "verification": "testing", "reproducibility": "testing", "evaluation": "testing",
    "benchmarking": "testing", "compile-time-checks": "testing", "e2e": "testing",
    "github-actions": "ci-cd", "github-automation": "agents", "github-api": "agents",
    "workflow-design": "agents", "task-scheduling": "agents", "agentic-workflows": "agents",
    "mcp": "llm", "tool-calling": "llm", "structured-output": "llm", "gpu": "llm",
    "quantization": "llm", "model-management": "llm", "llm-serving": "llm",
    "self-hosting": "llm", "cost-analysis": "llm", "llm-tooling": "llm",
    "openapi": "documentation", "api-documentation": "documentation",
    "wiki-js": "documentation", "offline": "documentation", "http": "go",
    "backend": "go", "deployment": "architecture", "embed": "go", "chi": "go",
    "streaming": "api-design", "rest-api": "api-design", "proxy": "api-design",
    "reverse-proxy": "networking", "api-integration": "api-design",
    "webhooks": "api-design", "payments": "api-design", "e-commerce": "architecture",
    "analytics": "databases", "dashboards": "frontend", "reporting": "documentation",
    "pdf-generation": "documentation", "pdf": "documentation", "signal-processing": "embedded",
    "sensors": "embedded", "calibration": "embedded", "statistics": "performance",
    "spc": "performance", "process-monitoring": "observability", "simulation": "testing",
    "hardware-integration": "embedded", "real-time": "performance", "pipelines": "architecture",
    "configuration": "infrastructure-as-code", "configuration-management": "infrastructure-as-code",
    "local-development": "docker", "inotify": "linux", "git": "ci-cd",
    "build-systems": "ci-cd", "meta-repo": "ci-cd", "submodules": "ci-cd",
    "graph": "architecture", "daemon": "python", "stdlib": "python",
    "api-security": "security", "rate-limiting": "api-design", "scalability": "performance",
    "debugging": "reliability", "messaging": "redis", "mqtt": "redis",
    "mosquitto": "redis", "ssh": "security", "jinja2": "ansible", "ci": "ci-cd",
    "backend-for-frontend": "architecture", "monorepo": "architecture",
    "offline-first": "documentation", "asyncapi": "documentation", "chi-router": "go",
    "gdpr": "security", "sse": "observability", "guardrails": "agents",
    "llm-safety": "security", "llm-evaluation": "testing", "function-calling": "llm",
    "speculative-decoding": "llm", "llama-cpp": "llm", "model-swapping": "llm",
    "openai-api": "llm", "data-locality": "llm", "power-monitoring": "observability",
    "background-jobs": "python", "pytest": "testing", "docker-compose-dev": "docker",
    "stripe": "api-design", "routing": "frontend", "retitled": None,
}

# Sensible labels for the eight retitled articles, which carried a placeholder.
RETITLED = {
 "infrastructure-as-code": ["infrastructure-as-code","ansible","linux","reliability"],
 "kubernetes":             ["kubernetes","docker","architecture","gitops"],
 "ceph":                   ["ceph","storage","reliability","performance"],
 "observatory-monitoring": ["observability","alerting","reliability","testing"],
 "netbird-vpn":            ["networking","dns","security","linux"],
 "keycloak":               ["identity","security","architecture"],
 "atlas-agentic-ops":      ["llm","agents","testing","security"],
 "opentaberna":            ["documentation","testing","ci-cd","architecture"],
}

# Entries whose original labels all collapsed onto ONE tag. The fix is a genuinely
# different second dimension, not padding — a tag that does not distinguish anything is
# the same mistake as a tag used once.
SUPPLEMENT = {
 "uv-ruff-python-tooling":     ["ci-cd", "automation"],
 "python-cli-scaffolding":     ["architecture", "automation"],
 "llama-swap-model-swapping":  ["performance", "architecture"],
 "http-service-discovery":     ["automation", "networking"],
 "vcpkg-cross-compilation":    ["ci-cd", "embedded"],
 "design-tokens-discipline":   ["architecture", "documentation"],
 "dependency-aware-queueing":  ["automation", "architecture"],
}


def collapse(slug, labels):
    if slug in RETITLED:
        return RETITLED[slug]
    out = []
    for l in labels:
        t = MAP.get(l, l)
        if t and t in VOCAB and t not in out:
            out.append(t)
    for extra in SUPPLEMENT.get(slug, []):
        if extra not in out:
            out.append(extra)
    return out[:5]

path = pathlib.Path("/home/philipp/Schreibtisch/self/github-page/docs/blog-plan.json")
data = json.loads(path.read_text())
unmapped = collections.Counter()

for group in ("scheduled", "backlog"):
    for a in data[group]:
        before = a["labels"]
        a["labels"] = collapse(a.get("slug", ""), before)
        for l in before:
            t = MAP.get(l, l)
            if t is not None and t not in VOCAB:
                unmapped[l] += 1
        if len(a["labels"]) < 2:
            unmapped[f"TOO FEW: {a['slug']}"] += 1

data["vocabulary"] = VOCAB
path.write_text(json.dumps(data, indent=1))

c = collections.Counter(t for a in data["scheduled"] for t in a["labels"])
print(f"vocabulary: {len(VOCAB)} tags")
print(f"in use on the schedule: {len(c)}")
print(f"used once: {sum(1 for v in c.values() if v == 1)}")
if unmapped:
    print("UNMAPPED:", dict(unmapped))
print()
for k, v in c.most_common():
    print(f"  {v:3}  {k}")

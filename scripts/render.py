#!/usr/bin/env python3
import json, pathlib, collections
from datetime import date

d = json.loads(pathlib.Path("/home/philipp/Schreibtisch/self/github-page/docs/blog-plan.json").read_text())
sched, backlog, vocab = d["scheduled"], d["backlog"], d["vocabulary"]

SOURCES = {"iac": "ai_internal_IaC", "k8s": "ai-internal_k8s + observatory",
           "atlas": "atlas", "taberna": "OpenTaberna + apps", "horus": "horus + edge compute",
           "ygg": "yggdrasil + nf1 website", "existing": "already published"}

L = []
w = L.append
w("# Article plan\n")
w("108 articles: **100 new** plus the **8 already published**, which are rewritten and")
w("retitled so the whole blog reads as one series rather than two.\n")
w("Nothing here is written yet. Strike, reorder or retitle anything — this file is the")
w("cheapest place to change your mind. The research behind every entry is done, so a")
w("swap from the backlog costs nothing.\n")

w("## The rules each article follows\n")
w("| Rule | How it is met |")
w("| --- | --- |")
w("| Professional titles | Descriptive, technology named, no wordplay. The title states what the article is about, which is also what makes it findable. |")
w("| Problem → approach → solution → conclusion | Every article carries all four sections. The problem is stated in the table below. |")
w("| Code examples | Each article has at least one complete, runnable example with pinned versions — Dockerfiles, manifests, playbooks, scripts. |")
w("| Reconstructable by anyone | Examples are self-contained and use no internal address, hostname, credential or topology. A reader reproduces them with public software only. |")
w("| Indexable | Descriptive titles, per-article meta description, BlogPosting structured data, controlled labels, internal links between related articles. |")
w("| Sensible labels | A closed 35-tag vocabulary, below. No tag is used only once. |")
w("| Weekly cadence | Two per week, Tuesdays and Fridays, from 2025-08-26 to 2026-09-04. |")
w("")

w("## Ordering\n")
w("Articles are placed so that no article describes something that did not exist yet at")
w("its date. Foundations first — Linux, Ansible, Docker, Python — then storage, secrets")
w("and the C++/Redis pipeline work, then Kubernetes, the mesh, identity, the")
w("observability platform and the agent workflows. The estate-wide overview is last,")
w("because it links the others.\n")

w("## Label vocabulary\n")
counts = collections.Counter(t for a in sched for t in a["labels"])
w("Closed set. An article may only carry tags from it.\n")
w("| Tag | Articles | Tag | Articles | Tag | Articles |")
w("| --- | ---: | --- | ---: | --- | ---: |")
ordered = sorted(vocab)
for i in range(0, len(ordered), 3):
    row = ordered[i:i + 3]
    while len(row) < 3:
        row.append(None)
    w("| " + " | ".join(f"`{t}` | {counts[t]}" if t else " | " for t in row) + " |")
w("")

w("## Schedule\n")
cur = None
for a in sched:
    y, m = a["date"][:4], a["date"][5:7]
    month = date(int(y), int(m), 1).strftime("%B %Y")
    if month != cur:
        cur = month
        w(f"\n### {month}\n")
        w("| # | Date | Title | Labels | Source |")
        w("| ---: | --- | --- | --- | --- |")
    labels = " ".join(f"`{t}`" for t in a["labels"])
    src = SOURCES.get(a["source"], a["source"])
    star = " **↺**" if a["source"] == "existing" else ""
    w(f"| {a['n']} | {a['date']} | **{a['title']}**{star}<br>{a['problem']} | {labels} | {src} |")

w("\n**↺** marks one of the eight already-published articles, retitled and rewritten.\n")

w("## Backlog\n")
w(f"{len(backlog)} topics that did not fit the 108 slots. The research is identical;")
w("only the calendar is full. Swap any of these for a scheduled one.\n")
w("| Title | Labels | Source |")
w("| --- | --- | --- |")
for a in backlog:
    labels = " ".join(f"`{t}`" for t in a["labels"])
    w(f"| {a['title']}<br>{a['problem']} | {labels} | {SOURCES.get(a['source'], a['source'])} |")

w("\n## Sources\n")
sc = collections.Counter(SOURCES.get(a["source"], a["source"]) for a in sched)
w("| Repository | Articles | Visibility |")
w("| --- | ---: | --- |")
vis = {"ai_internal_IaC": "private", "ai-internal_k8s + observatory": "private",
       "atlas": "private", "OpenTaberna + apps": "OpenTaberna public, others mixed",
       "horus + edge compute": "private", "yggdrasil + nf1 website": "private",
       "already published": "—"}
for k, v in sc.most_common():
    w(f"| {k} | {v} | {vis.get(k, '')} |")
w("")
w("Four of the source repositories are private company repositories. **No address,")
w("hostname, credential, port, topology detail or customer name from any of them appears")
w("in any article.** The articles carry the engineering and the reasoning; the code")
w("examples are written from scratch to be reproducible by a stranger, which is a")
w("stricter requirement than anonymisation and happens to satisfy both.")

out = pathlib.Path("/home/philipp/Schreibtisch/self/github-page/docs/blog-plan.md")
out.write_text("\n".join(L) + "\n")
print(f"{out}: {len(L)} lines, {len(sched)} scheduled, {len(backlog)} backlog")

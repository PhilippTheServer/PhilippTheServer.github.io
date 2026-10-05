"""What a Medium draft of an article must contain, read from the article's built page.

The rendered page is the single source: its body is what gets pasted into Medium, and its
title, subtitle, meta description and tags equal the front matter. Pure functions only;
the browser side lives in medium.py.
"""

import copy
import html
import re
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

SITE = "https://philipptheserver.com"

# Medium's code block languages. Languages it lacks (Jinja, Dockerfile, nginx, HCL, CMake,
# text) and blocks without a language are shown as Plain Text.
MEDIUM_LANGUAGE = {
    "bash": "Bash",
    "python": "Python",
    "yaml": "YAML",
    "json": "JSON",
    "go": "Go",
    "typescript": "TypeScript",
    "cpp": "C++",
    "sql": "SQL",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "html": "HTML, XML",
    "lua": "Lua",
    "diff": "Diff",
    "css": "CSS",
    "ini": "TOML, INI",
    "toml": "TOML, INI",
}

# Site tag -> Medium topic, approved 2026-09-30. Medium allows five topics; posts with fewer
# tags are filled with broad topics, code-leaning posts with programming ones.
MEDIUM_TOPIC = {
    "agents": "AI Agent",
    "alerting": "Monitoring",
    "ansible": "Ansible",
    "api-design": "API",
    "architecture": "Software Architecture",
    "automation": "Automation",
    "bluetooth": "Bluetooth",
    "ceph": "Ceph",
    "ci-cd": "Continuous Integration",
    "cpp": "Cpp",
    "databases": "Database",
    "dns": "DNS",
    "docker": "Docker",
    "documentation": "Documentation",
    "embedded": "Embedded Systems",
    "fastapi": "Fastapi",
    "flutter": "Flutter",
    "frontend": "Frontend",
    "gitops": "Gitops",
    "go": "Golang",
    "identity": "Keycloak",
    "infrastructure-as-code": "Infrastructure As Code",
    "kubernetes": "Kubernetes",
    "linux": "Linux",
    "llm": "LLM",
    "methodology": "Engineering",
    "networking": "Networking",
    "observability": "Observability",
    "performance": "Performance",
    "python": "Python",
    "redis": "Redis",
    "reliability": "Site Reliability Engineer",
    "reverse-engineering": "Reverse Engineering",
    "secrets-management": "Hashicorp Vault",
    "security": "Cybersecurity",
    "storage": "Storage",
    "testing": "Software Testing",
    "tls": "Security",
    "vault": "Hashicorp Vault",
}
CODE_TAGS = {"python", "go", "cpp", "fastapi", "api-design", "frontend", "databases"}
FILL_CODE = ["Software Engineering", "Programming", "DevOps"]
FILL_INFRA = ["DevOps", "Software Engineering", "Programming"]

# The site shows tabs at this width (prose.css: tab-size). Medium stores a tab as one space.
TAB_WIDTH = 2


def norm_text(text):
    """Text as compared between site and Medium: typographic quotes and dashes, no-break and
    figure spaces folded, whitespace collapsed."""
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u2014", "-"), ("\u2013", "-")):
        text = text.replace(a, b)
    text = text.replace("\u00a0", " ").replace("\u2007", " ").replace("\u2060", "")
    return " ".join(text.split())


def squash(parts):
    return "".join("".join(parts).split())


def inline(el):
    """Inline formatting as text, so one <strong> that Medium splits around a <code> still
    compares equal."""
    return {
        "links": squash(a.get_text() for a in el.find_all("a")),
        "code": squash(c.get_text() for c in el.find_all("code")),
        "strong": squash(s.get_text() for s in el.find_all(["strong", "b"])),
        "em": squash(e.get_text() for e in el.find_all(["em", "i"])),
    }


def decode_pre(pre):
    """The exact source text of a code block, undoing the encoding Medium's importer needs
    (#56): <br> per line, figure spaces, a word joiner plus four figure spaces per tab, and a
    lone no-break space on an empty line. The same rules as assets/js/highlight.js."""
    inner = re.sub(r"<br\s*/?>", "\n", pre.decode_contents())
    text = html.unescape(re.sub(r"<[^>]+>", "", inner))
    lines = []
    for line in text.split("\n"):
        if line == "\u00a0":
            lines.append("")
        else:
            lines.append(line.replace("\u2060\u2007\u2007\u2007\u2007", "\t").replace("\u2007", " "))
    return "\n".join(lines)


def language_of(pre):
    return MEDIUM_LANGUAGE.get(pre.get("data-lang") or "", "Plain Text")


def render_table(table):
    """Medium has no table element: a table becomes aligned columns in a plain-text block."""
    rows = [[" ".join(c.get_text().split()) for c in r.find_all(["th", "td"])] for r in table.find_all("tr")]
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    width = [max(len(r[i]) for r in rows) for i in range(n)]

    def line(row):
        return "  ".join(c.ljust(width[i]) for i, c in enumerate(row)).rstrip()

    return "\n".join([line(rows[0]), "  ".join("-" * w for w in width)] + [line(r) for r in rows[1:]])


def clean(el):
    """An element as paste HTML: no attributes except link targets."""
    el = copy.copy(el)
    for tag in [el] + el.find_all(True):
        for attr in list(tag.attrs):
            if not (tag.name == "a" and attr == "href"):
                del tag[attr]
    return str(el)


def topics(tags):
    out = list(dict.fromkeys(MEDIUM_TOPIC[t] for t in tags))
    for broad in FILL_CODE if CODE_TAGS & set(tags) else FILL_INFRA:
        if len(out) < 5 and broad not in out:
            out.append(broad)
    return out[:5]


def article(page_html, slug):
    """The model of one article: metadata, the block sequence the draft must have, the code
    each block holds, and the HTML to paste. Code blocks and tables are pasted as
    placeholders (a pasted <pre> ends at its first blank line) and filled afterwards."""
    soup = BeautifulSoup(page_html, "html.parser")
    body = soup.select_one(".post-body.prose")
    seq, fills, parts = [], [], []

    def fill(text, label):
        fills.append({"text": text.expandtabs(TAB_WIDTH), "label": label})
        seq.append({"kind": "fill", "fill": len(fills) - 1})
        parts.append(f"<pre>CODEBLOCK-{len(fills) - 1}</pre>")

    for el in body.find_all(recursive=False):
        if el.name == "pre":
            fill(decode_pre(el), language_of(el))
        elif el.name == "div" and el.find("table"):
            fill(render_table(el.find("table")), "Plain Text")
        elif el.name in ("ul", "ol"):
            items = []
            for li in el.find_all("li", recursive=False):
                li = copy.copy(li)
                nested = [p.extract() for p in li.find_all("pre")]
                seq.append({"kind": "li", "text": norm_text(li.get_text()), **inline(li)})
                items.append(clean(li))
                for pre in nested:  # Medium has no code inside a list item: split the list
                    parts.append(f"<{el.name}>{''.join(items)}</{el.name}>")
                    items = []
                    fill(decode_pre(pre), language_of(pre))
            if items:
                parts.append(f"<{el.name}>{''.join(items)}</{el.name}>")
        elif el.name in ("h2", "h3", "h4", "p"):
            seq.append({"kind": el.name, "text": norm_text(el.get_text()), **inline(el)})
            parts.append(clean(el))
        else:
            raise ValueError(f"{slug}: no Medium equivalent for top-level <{el.name}>")

    tags = [a.get_text() for a in soup.select(".post-tags a")]
    return {
        "slug": slug,
        "url": f"{SITE}/posts/{slug}/",
        "title": soup.select_one("h1.page-title").get_text().strip(),
        "subtitle": " ".join(soup.select_one("p.page-subtitle").get_text().split()),
        "description": " ".join(soup.select_one('meta[name="description"]')["content"].split()),
        "tags": tags,
        "topics": topics(tags),
        "seq": seq,
        "fills": fills,
        "html": "".join(parts),
    }


def fetch(slug):
    with urllib.request.urlopen(f"{SITE}/posts/{slug}/") as r:
        return article(r.read().decode(), slug)


def canonical_problem(links, url):
    """None if a published story's canonical links are exactly the article's URL."""
    if links == [url]:
        return None
    if not links:
        return "no canonical link"
    return f"canonical {links} should be {url}"


def unwrap_medium_link(href):
    """Medium routes outbound links through medium.com/r/?url=…; the target is the parameter."""
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc == "medium.com" and parsed.path == "/r/":
        return urllib.parse.parse_qs(parsed.query).get("url", [href])[0]
    return href

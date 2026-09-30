#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.63", "beautifulsoup4>=4.15"]
# ///
"""Prepare and verify the Medium drafts of the site's articles (#68).

  medium.py browser                     start the automation Chrome (sign in to Medium once)
  medium.py sync SLUG... | --all        import or repair drafts until they match the site
  medium.py verify SLUG... | --all      compare drafts with the site, change nothing

Drafts only: a published story is never edited; its canonical link is checked instead.
Which story belongs to which article is kept in ~/.local/state/medium-sync/.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import site_model as sm

REPO = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "medium-sync" / "state.json"
PROFILE = Path.home() / ".local/share/medium-import-browser"
CDP = "http://127.0.0.1:9222"
CHROME = "/opt/google/chrome/chrome"
WANT_TAG = {"h2": "H3", "h3": "H4", "h4": "H4", "p": "P", "li": "LI", "fill": "PRE"}


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


def all_slugs():
    return [p.name[11:-3] for p in sorted((REPO / "_posts").glob("*.md"))]


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)


# ---------- browser ----------


def browser_up():
    try:
        urllib.request.urlopen(f"{CDP}/json/version", timeout=2)
        return True
    except OSError:
        return False


def start_browser():
    """Google Chrome, not Playwright's Chrome for Testing: Ubuntu's AppArmor only lets the
    packaged Chrome use its sandbox. Detached, so the shell that starts it can end."""
    if browser_up():
        log("Chrome is already running")
        return
    subprocess.Popen(
        [
            CHROME,
            f"--user-data-dir={PROFILE}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222",
            "--no-first-run",
            "--no-default-browser-check",
            "https://medium.com/me/stories/drafts",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        if browser_up():
            log(f"Chrome started with profile {PROFILE}; sign in to Medium there if it asks")
            return
        time.sleep(0.5)
    sys.exit("Chrome did not open its debugging port")


@contextmanager
def connect():
    if not browser_up():
        sys.exit("no automation Chrome; run: medium.py browser")
    with sync_playwright() as p:
        ctx = p.chromium.connect_over_cdp(CDP).contexts[0]
        pages = [pg for pg in ctx.pages if not pg.url.startswith("devtools://")]
        pg = pages[0] if pages else ctx.new_page()
        global TRACKER
        TRACKER = SaveTracker(pg)
        yield pg


# ---------- saving ----------


class SaveTracker:
    """Pending save requests. Medium saves edits as POST .../deltas; a language change does so
    without showing 'Saving'. A request cancelled by a reload never reports back, so entries
    expire and navigation clears them."""

    def __init__(self, pg):
        self.pending, self.last = {}, time.time()
        pg.on("request", self._start)
        pg.on("requestfinished", self._end)
        pg.on("requestfailed", self._end)
        pg.on("framenavigated", lambda f: self.pending.clear() if f == pg.main_frame else None)

    @staticmethod
    def _is_save(r):
        return r.method == "POST" and "/deltas" in r.url

    def _start(self, r):
        if self._is_save(r):
            self.pending[id(r)] = self.last = time.time()

    def _end(self, r):
        if self._is_save(r):
            self.pending.pop(id(r), None)
            self.last = time.time()

    def idle(self):
        now = time.time()
        self.pending = {k: t for k, t in self.pending.items() if now - t < 20}
        return not self.pending and now - self.last > 3


TRACKER = None
SAVING = """() => [...document.querySelectorAll('span, div, p')].filter(e => e.children.length === 0
    && e.getBoundingClientRect().top < 90 && /^Saving/.test((e.innerText || '').trim())).length"""


def wait_saved(pg, timeout=90):
    """Leaving the editor while a save is pending loses it and later shows 'You've edited
    this story elsewhere'."""
    if "/edit" not in pg.url:
        return
    pg.wait_for_timeout(1500)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if TRACKER.idle() and not pg.evaluate(SAVING):
            return
        pg.wait_for_timeout(500)
    raise RuntimeError(f"Medium still saving after {timeout} s")


def open_editor(pg, story):
    if f"/p/{story}/edit" not in pg.url:
        wait_saved(pg)
        pg.goto(f"https://medium.com/p/{story}/edit", wait_until="domcontentloaded")
    pg.wait_for_selector(".section-inner .graf--title", timeout=30000)
    pg.wait_for_timeout(1500)
    if pg.get_by_text("edited this story elsewhere", exact=False).count():
        log("  conflict dialog: reloading the story")
        pg.get_by_text("Reload the story to see changes", exact=False).first.click()
        pg.wait_for_selector(".section-inner .graf--title", timeout=30000)
        pg.wait_for_timeout(2000)


def reload_editor(pg, story):
    wait_saved(pg)
    pg.reload(wait_until="domcontentloaded")
    open_editor(pg, story)


# ---------- stories ----------


def list_titles(pg, tab):
    """Title -> story ids on the Drafts or Published tab of the stories page. The list loads
    as it scrolls, so it is read until it holds as many stories as the tab's own count says;
    an incomplete list could make an existing draft look missing and import it twice."""
    pg.goto("https://medium.com/me/stories/drafts", wait_until="domcontentloaded")
    pg.get_by_text(re.compile(r"^Drafts\s*\d*$")).first.wait_for(timeout=30000)
    pg.wait_for_timeout(2000)
    tabs = " ".join(pg.inner_text("body").split())
    count = re.search(rf"\b{tab} (\d+) ", tabs) or re.search(rf"\b{tab} (?=[A-Z])", tabs)
    if not count:
        raise RuntimeError(f"no {tab} tab on the stories page")
    expected = int(count.group(1)) if count.lastindex else 0  # Medium shows no number for an empty tab
    if tab != "Drafts":
        pg.get_by_text(re.compile(rf"^{tab}\s*\d*$")).first.click()
        pg.wait_for_timeout(3000)
    rows = []
    for _ in range(120):
        rows = pg.evaluate("""() => [...document.querySelectorAll('a[href]')].map(a => [(a.innerText || '').split('\\n')[0].trim(),
            (a.getAttribute('href').match(/\\/p\\/([0-9a-f]{8,})|-([0-9a-f]{10,})(?:\\?|$)/) || []).slice(1).find(Boolean)])
            .filter(([t, id]) => t && id)""")
        if len({story for _, story in rows}) >= expected:
            break
        pg.mouse.wheel(0, 20000)
        pg.wait_for_timeout(1500)
    else:
        raise RuntimeError(f"{tab}: listed {len({s for _, s in rows})} stories, the tab says {expected}")
    out = {}
    for title, story in rows:
        out.setdefault(sm.norm_text(title), set()).add(story)
    return out


def import_story(pg, url):
    pg.goto("https://medium.com/p/import", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    pg.get_by_text("http://www.yoursite.org/your-post", exact=True).click()
    pg.keyboard.type(url, delay=10)
    # A normal click waits for the import's navigation, which can outlast any timeout.
    pg.get_by_role("button", name="Import", exact=True).evaluate("b => b.click()")
    for _ in range(120):
        pg.wait_for_timeout(1000)
        if pg.get_by_text("See your story", exact=True).count():
            break
        if "import failed" in pg.inner_text("body").lower():
            raise RuntimeError(f"Medium says import failed for {url}")
    else:
        raise RuntimeError(f"import of {url} did not finish in 120 s")
    pg.get_by_text("See your story", exact=True).last.evaluate("e => (e.closest('a, button') || e).click()")
    pg.wait_for_url(re.compile(r"/p/[0-9a-f]+/edit"), timeout=30000)
    return re.search(r"/p/([0-9a-f]+)/edit", pg.url).group(1)


# ---------- body ----------

GRAFS = """() => { const sq = (e, sel) => [...e.querySelectorAll(sel)].filter(x => !x.parentElement.closest(sel))
      .map(x => x.innerText).join('').replace(/\\s+/g, '');
  return [...document.querySelectorAll('.section-inner .graf')].map(e => {
    const text = (e.tagName === 'PRE' ? e.querySelector('.pre--content')?.innerText : e.innerText) || '';
    return {name: e.getAttribute('name'), tag: e.tagName, title: e.classList.contains('graf--title'),
      empty: e.classList.contains('graf--empty') || !text.trim(), text,
      label: e.querySelector('.codeBlockMenu-button')?.innerText.trim() || null,
      hrefs: [...e.querySelectorAll('a')].map(a => a.href),
      links: sq(e, 'a'), code: sq(e, 'code'), strong: sq(e, 'strong, b'), em: sq(e, 'em, i')}; }); }"""


def body_grafs(pg):
    grafs = pg.evaluate(GRAFS)
    if not grafs or not grafs[0]["title"]:
        raise RuntimeError("first block is not the title")
    return grafs, grafs[-1]["text"].startswith("Originally published at")


def compare(pg, art):
    grafs, footer = body_grafs(pg)
    problems = [] if footer else ["no 'Originally published at' footer"]
    if footer:
        # The footer links to the URL Medium imported from, which is also what it makes the
        # canonical link once the story is published.
        problem = sm.canonical_problem([sm.unwrap_medium_link(h) for h in grafs[-1]["hrefs"]], art["url"])
        if problem:
            problems.append("footer: " + problem.replace("canonical ", "links to "))
    empties = sum(g["empty"] for g in grafs)
    if empties:
        problems.append(f"{empties} empty blocks")
    body = [g for g in grafs[1 : -1 if footer else None] if not g["empty"]]
    for i in range(max(len(art["seq"]), len(body))):
        s = art["seq"][i] if i < len(art["seq"]) else None
        m = body[i] if i < len(body) else None
        if s is None or m is None:
            problems.append(
                f"#{i}: site {s and s['kind']} vs Medium {m and m['tag']} {(m or {}).get('text', '')[:40]!r}"
            )
            break
        want = WANT_TAG[s["kind"]]
        if m["tag"] != want:
            problems.append(f"#{i}: {m['tag']} should be {want} ({(s.get('text') or '')[:40]!r})")
            if "PRE" in (m["tag"], want):
                break
            continue
        if s["kind"] == "fill":
            f = art["fills"][s["fill"]]
            if m["text"] != f["text"]:
                problems.append(f"#{i}: code block {s['fill']} text differs")
            if m["label"] != f["label"]:
                problems.append(f"#{i}: code block {s['fill']} label {m['label']!r} should be {f['label']!r}")
            continue
        got = sm.norm_text(m["text"])
        if got != s["text"]:
            k = next((j for j, (x, y) in enumerate(zip(s["text"], got)) if x != y), min(len(s["text"]), len(got)))
            problems.append(
                f"#{i}: text differs at {k}: site {s['text'][k - 20 : k + 20]!r} Medium {got[k - 20 : k + 20]!r}"
            )
            continue
        if s["kind"] in ("h2", "h3", "h4"):
            continue  # Medium headings cannot hold inline formatting; their text is compared above
        keys = ("links", "code", "strong", "em")
        wrong = [k for k in keys if m[k] != s[k]]
        if wrong:
            problems.append(f"#{i}: inline formatting differs: {', '.join(wrong)} ({s['text'][:40]!r})")
    return problems


def paste(pg, name, html=None, text=None, at_start=False):
    """A synthetic paste event: Medium's editor handles it like a real one, and neither the
    system clipboard nor window focus is involved."""
    return pg.evaluate(
        """([n, html, text, atStart]) => {
        const g = document.querySelector('.graf[name="' + n + '"]');
        const t = g.querySelector('.pre--content') || g;
        const r = document.createRange(); r.selectNodeContents(t); if (atStart) r.collapse(true);
        const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        const dt = new DataTransfer(); if (html) dt.setData('text/html', html); dt.setData('text/plain', text || '');
        const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
        t.dispatchEvent(ev); return ev.defaultPrevented; }""",
        [name, html, text, at_start],
    )


def replace_body(pg, art):
    """Everything between the title and the 'Originally published at' footer is replaced by
    the site's article; the import's own body is never trusted (Medium caches first imports)."""
    grafs, footer = body_grafs(pg)
    if not footer:
        raise RuntimeError("no 'Originally published at' footer; refusing to replace the body")
    if len(grafs) > 2:
        first, last = grafs[1]["name"], grafs[-2]["name"]
        pg.locator(f'.graf[name="{first}"]').scroll_into_view_if_needed()
        pg.locator(f'.graf[name="{first}"]').click()
        pg.evaluate(
            """([a, b]) => { const A = document.querySelector('.graf[name="' + a + '"]');
            const B = document.querySelector('.graf[name="' + b + '"]'); const r = document.createRange();
            r.setStart(A, 0); r.setEnd(B, B.childNodes.length);
            const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }""",
            [first, last],
        )
        pg.wait_for_timeout(300)
        pg.keyboard.press("Backspace")
        pg.wait_for_timeout(1200)
    grafs, _ = body_grafs(pg)
    if len(grafs) == 2:
        pg.evaluate(
            """n => { const g = document.querySelector('.graf[name="' + n + '"]'); const r = document.createRange();
            r.selectNodeContents(g); r.collapse(false); const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }""",
            grafs[0]["name"],
        )
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(600)
        grafs, _ = body_grafs(pg)
    if len(grafs) != 3 or not grafs[1]["empty"]:
        raise RuntimeError(f"clearing left {[(g['tag'], g['text'][:20]) for g in grafs][:6]}")
    if not paste(pg, grafs[1]["name"], html=art["html"], at_start=True):
        raise RuntimeError("Medium did not handle the body paste")
    pg.wait_for_timeout(4000)


def fill_blocks(pg, art):
    grafs, _ = body_grafs(pg)
    for g in grafs:
        m = re.fullmatch(r"CODEBLOCK-(\d+)\s*", g["text"]) if g["tag"] == "PRE" else None
        if m:
            pg.locator(f'.graf[name="{g["name"]}"]').scroll_into_view_if_needed()
            paste(pg, g["name"], text=art["fills"][int(m.group(1))]["text"])
            pg.wait_for_timeout(700)


def delete_empty_blocks(pg):
    """The importer and the paste leave empty blocks; each goes with one Backspace, checked
    so that no text anywhere changes."""
    caret = """() => { const n = window.getSelection().anchorNode;
        const g = n && (n.nodeType === 1 ? n : n.parentElement).closest('.graf'); return g && g.getAttribute('name') }"""
    for _ in range(300):
        grafs, _ = body_grafs(pg)
        empty = [g for g in grafs if g["empty"]]
        if not empty:
            return
        g = empty[0]
        loc = pg.locator(f'.graf[name="{g["name"]}"]')
        loc.scroll_into_view_if_needed()
        loc.click(position={"x": 5, "y": 5})
        pg.wait_for_timeout(200)
        if pg.evaluate(caret) != g["name"]:
            raise RuntimeError(f"could not place the caret in empty block {g['name']}")
        pg.keyboard.press("Backspace")
        pg.wait_for_timeout(500)
        after, _ = body_grafs(pg)
        if [(a["tag"], a["text"]) for a in after if not a["empty"]] != [
            (b["tag"], b["text"]) for b in grafs if not b["empty"]
        ]:
            pg.keyboard.press("Control+z")
            raise RuntimeError(f"deleting empty block {g['name']} changed text; undone")


def set_label(pg, name, label):
    pre = pg.locator(f'pre[name="{name}"]')
    button = pre.locator(".codeBlockMenu-button")
    for _ in range(3):
        pg.keyboard.press("Escape")
        # Select the block with the caret: the top of a tall block sits under Medium's fixed
        # header bar, which swallows clicks. The language button appears once it is selected.
        pg.evaluate(
            """n => { const c = document.querySelector('pre[name="' + n + '"] .pre--content');
            c.scrollIntoView({block: 'center'}); const r = document.createRange(); r.selectNodeContents(c); r.collapse(true);
            const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }""",
            name,
        )
        pg.wait_for_timeout(400)
        if not (button.bounding_box() or {}).get("width"):
            box = pre.bounding_box()
            pg.mouse.click(box["x"] + 30, max(box["y"] + 20, 120))
            pg.wait_for_timeout(300)
        button.evaluate("e => e.scrollIntoView({block: 'center'})")
        pg.wait_for_timeout(300)
        b = button.bounding_box()
        if b and b["width"]:
            pg.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
            item = pg.locator(".popover.is-active").get_by_text(label, exact=True)
            try:
                item.first.wait_for(timeout=3000)
                item.first.click()
            except PlaywrightError:
                pg.wait_for_timeout(700)
                continue
            pg.wait_for_timeout(500)
            if button.inner_text().strip() == label:
                wait_saved(pg)  # label changes saved together overwrite each other
                return
        pg.wait_for_timeout(700)
    raise RuntimeError(f"could not set code block {name} to {label!r}")


def fix_headings_and_labels(pg, art):
    grafs, footer = body_grafs(pg)
    body = [g for g in grafs[1 : -1 if footer else None] if not g["empty"]]
    if len(body) != len(art["seq"]):
        return
    for s, g in zip(art["seq"], body):
        if WANT_TAG[s["kind"]] == "H4" and g["tag"] == "H3":
            loc = pg.locator(f'.graf[name="{g["name"]}"]')
            loc.scroll_into_view_if_needed()
            loc.click()
            pg.wait_for_timeout(150)
            pg.keyboard.press("Control+Alt+2")
            pg.wait_for_timeout(400)
        elif s["kind"] == "fill" and g["label"] != art["fills"][s["fill"]]["label"]:
            try:
                set_label(pg, g["name"], art["fills"][s["fill"]]["label"])
            except RuntimeError as e:
                log(f"  {e}")


def sync_body(pg, story, art, rounds=4):
    open_editor(pg, story)
    problems = compare(pg, art)
    for n in range(rounds):
        if not problems:
            return []
        log(f"  round {n + 1}: {len(problems)} problems, e.g. {problems[:3]}")
        grafs, _ = body_grafs(pg)
        if any(g["tag"] == "PRE" and re.fullmatch(r"CODEBLOCK-\d+\s*", g["text"]) for g in grafs):
            fill_blocks(pg, art)
        elif not all(re.search(r"empty blocks|: H3 should be H4 |label", p) for p in problems):
            replace_body(pg, art)
            wait_saved(pg)
            fill_blocks(pg, art)
        wait_saved(pg)
        delete_empty_blocks(pg)
        fix_headings_and_labels(pg, art)
        reload_editor(pg, story)
        problems = compare(pg, art)
    return problems


# ---------- metadata ----------


def set_subtitle(pg, story, subtitle):
    open_editor(pg, story)
    pg.locator('button[data-action="show-post-actions-popover"]').first.click()
    pg.wait_for_timeout(600)
    pg.locator('[data-action="show-metadata-popover"]').first.click()
    pg.wait_for_timeout(1000)
    field = pg.locator(".popover--customTitleControl.is-active .customTitleControl-field").nth(1)
    field.click()
    pg.keyboard.press("Control+a")
    pg.keyboard.insert_text(subtitle)
    pg.wait_for_timeout(300)
    pg.locator(".popover--customTitleControl.is-active").get_by_text("Done", exact=True).click()
    pg.wait_for_timeout(1500)


SEO_TITLE = "input[placeholder*='| Medium']"
SEO_DESCRIPTION = "textarea"


def open_settings(pg, story):
    wait_saved(pg)
    pg.goto(f"https://medium.com/p/{story}/settings", wait_until="domcontentloaded")
    pg.get_by_text("Add up to five topics", exact=False).first.wait_for(timeout=30000)
    pg.wait_for_timeout(1500)


def save_field(pg, selector, value):
    field = pg.locator(selector).first
    field.scroll_into_view_if_needed()
    field.click()
    pg.keyboard.press("Control+a")
    pg.keyboard.press("Delete")
    pg.keyboard.insert_text(value)
    pg.wait_for_timeout(300)
    field.locator("xpath=following::button[normalize-space()='Save'][1]").click()
    pg.wait_for_timeout(1500)


def current_topics(pg):
    """Read from the page text: the topic field itself disappears once five are set."""
    for _ in range(20):
        text = pg.inner_text("body")
        a = text.find("Add up to five topics to help readers find your story.")
        b = text.find("SEO Settings", a)
        if a >= 0 and b > a:
            lines = (s.strip() for s in text[a:b].split("\n")[1:])
            return list(dict.fromkeys(s for s in lines if s and s not in ("\u00d7", "SEO Settings")))
        pg.wait_for_timeout(300)
    raise RuntimeError("Reader Interests section not found")


def add_topic(pg, topic):
    field = pg.locator("input[aria-controls=tagMultiSelectMenu]")
    field.scroll_into_view_if_needed()
    field.focus()
    pg.keyboard.press("Control+a")
    pg.keyboard.press("Delete")
    pg.keyboard.type(topic, delay=30)
    pg.wait_for_timeout(1800)
    option = pg.locator("#tagMultiSelectMenu button").filter(
        has_text=re.compile(rf"^{re.escape(topic)} \([\d.]+[KM]?\)$")
    )
    if option.count() != 1:
        pg.keyboard.press("Control+a")
        pg.keyboard.press("Delete")
        return False
    option.click()
    for _ in range(10):
        pg.wait_for_timeout(500)
        if topic in current_topics(pg):
            return True
    return False


def meta_problems(pg, art):
    problems = []
    if art["subtitle"] not in " ".join(pg.inner_text("body").split()):
        problems.append("subtitle not in the story preview")
    if pg.locator(SEO_TITLE).first.input_value() != art["title"]:
        problems.append("SEO title differs")
    if pg.locator(SEO_DESCRIPTION).first.input_value() != art["description"]:
        problems.append("SEO description differs")
    got = current_topics(pg)
    if sorted(got) != sorted(art["topics"]):
        problems.append(f"topics {got} should be {art['topics']}")
    return problems


def sync_meta(pg, story, art):
    set_subtitle(pg, story, art["subtitle"])
    open_settings(pg, story)
    if pg.locator(SEO_TITLE).first.input_value() != art["title"]:
        save_field(pg, SEO_TITLE, art["title"])
    if pg.locator(SEO_DESCRIPTION).first.input_value() != art["description"]:
        save_field(pg, SEO_DESCRIPTION, art["description"])
    for topic in art["topics"]:
        have = current_topics(pg)
        if topic not in have and len(have) < 5 and not any(add_topic(pg, topic) for _ in range(3)):
            log(f"  could not add topic {topic!r}")
    pg.reload(wait_until="domcontentloaded")
    pg.get_by_text("Add up to five topics", exact=False).first.wait_for(timeout=30000)
    pg.wait_for_timeout(2000)
    return meta_problems(pg, art)


# ---------- commands ----------


def story_for(pg, art, state, drafts, published, create):
    """The article's story and whether it is a draft or published: from the state file, else
    by title. Never a second import of an article Medium already has."""
    title = sm.norm_text(art["title"])
    story = state.get(art["slug"], {}).get("story")
    live = published.get(title, set())
    if story and any(story in ids for ids in published.values()):
        return story, "published"
    if live:
        return (
            (next(iter(live)), "published")
            if len(live) == 1
            else (None, f"{len(live)} published stories share this title")
        )
    ids = drafts.get(title, set())
    if story and story not in ids:
        return None, f"story {story} from the state file is not among the drafts"
    if not story:
        if len(ids) > 1:
            return None, f"{len(ids)} drafts share this title: {sorted(ids)}"
        story = next(iter(ids)) if ids else (import_story(pg, art["url"]) if create else None)
    return (story, "draft") if story else (None, "no draft")


def canonical_of(pg, story):
    """The canonical links of a published story's public page. Medium sets them when it imports
    an article, to the URL it fetched, and offers no way to change them afterwards."""
    wait_saved(pg)
    pg.goto(f"https://medium.com/p/{story}", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    return pg.evaluate(
        """() => [...document.querySelectorAll('link[rel="canonical"]')].map(l => l.getAttribute('href'))"""
    )


def run(slugs, create):
    state = load_state()
    failed, published = [], []
    with connect() as pg:
        drafts, live = list_titles(pg, "Drafts"), list_titles(pg, "Published")
        for slug in slugs:
            t0 = time.time()
            story = None
            try:
                art = sm.fetch(slug)
                story, kind = story_for(pg, art, state, drafts, live, create)
                if story:
                    state.setdefault(slug, {})["story"] = story
                    save_state(state)
                if kind == "published":
                    published.append(slug)
                    problem = sm.canonical_problem(canonical_of(pg, story), art["url"])
                    problems = [problem] if problem else []
                elif kind != "draft":
                    problems = [kind]
                elif create:
                    problems = sync_body(pg, story, art) + sync_meta(pg, story, art)
                else:
                    open_editor(pg, story)
                    problems = compare(pg, art)
                    open_settings(pg, story)
                    problems += meta_problems(pg, art)
            except (PlaywrightError, RuntimeError, OSError, ValueError) as e:  # one article must not stop the rest
                if "has been closed" in str(e):
                    sys.exit(f"{slug}: the browser was closed; rerun to continue")
                problems = [f"{type(e).__name__}: {str(e)[:300]}"]
            label = "published, left alone; canonical" if slug in published else "draft"
            status = "OK" if not problems else f"PROBLEMS {problems[:5]}"
            log(f"{slug} [{story}] {label} {status} in {time.time() - t0:.0f}s")
            if problems:
                failed.append(slug)
    bad_live = [s for s in failed if s in published]
    bad_drafts = [s for s in failed if s not in published]
    drafts_checked = len(slugs) - len(published)
    log(
        f"{drafts_checked - len(bad_drafts)} of {drafts_checked} drafts match the site; "
        f"{len(published) - len(bad_live)} of {len(published)} published stories have the article as canonical"
        + (f"; not OK: {failed}" if failed else "")
    )
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("browser", help="start the automation Chrome")
    for name in ("sync", "verify"):
        p = sub.add_parser(name)
        p.add_argument("slugs", nargs="*", help="article slugs, e.g. clock-jump-scheduler")
        p.add_argument("--all", action="store_true", help="every article in _posts")
    args = parser.parse_args()
    if args.command == "browser":
        start_browser()
        return 0
    slugs = all_slugs() if args.all else args.slugs
    unknown = set(slugs) - set(all_slugs())
    if not slugs or unknown:
        parser.error(f"unknown slugs: {sorted(unknown)}" if unknown else "name slugs or pass --all")
    return run(slugs, create=args.command == "sync")


if __name__ == "__main__":
    sys.exit(main())

"""The site-to-Medium model (#68). Run by check.sh."""

import re
from pathlib import Path

import pytest

import site_model as sm

REPO = Path(__file__).resolve().parents[2]

FS, WJ = "\u2007", "\u2060"
PAGE = f"""<html><head><meta name="description" content="A  description
  over two lines."></head><body>
<h1 class="page-title">A Title</h1><p class="page-subtitle">A subtitle.</p>
<span class="post-tags"><a href="/tags/python/">python</a><a href="/tags/testing/">testing</a></span>
<div class="post-body prose" itemprop="articleBody">
<h2 id="section">Section</h2>
<p class="x">Run <code>x</code> with <strong>bold <code>y</code> text</strong> and <a href="/a" class="l">a link</a>.</p>
<pre class="language-python" data-lang="python">def f():<br>{FS * 4}return 1<br>&nbsp;<br>{WJ}{FS * 4}x = 2</pre><p>After.</p>
<h3 id="sub">Sub</h3>
<ol><li><p>One</p></li><li><p>Two</p><pre class="language-bash" data-lang="bash">echo hi</pre></li><li><p>Three</p></li></ol>
<div class="table-wrapper"><table><tr><th>A</th><th>Bee</th></tr><tr><td>long cell</td><td>b</td></tr></table></div>
<pre>plain &amp; simple</pre>
</div></body></html>"""


@pytest.fixture
def art():
    return sm.article(PAGE, "a-slug")


def test_code_block_decodes_to_its_source_text(art):
    # figure spaces back to spaces, the lone no-break space back to an empty line,
    # word joiner plus four figure spaces back to a tab, expanded to the site's width
    assert art["fills"][0] == {"text": "def f():\n    return 1\n\n  x = 2", "label": "Python"}


def test_tabs_expand_to_the_site_tab_width():
    page = PAGE.replace("echo hi", f"a{WJ}{FS * 4}b<br>{WJ}{FS * 4}{WJ}{FS * 4}c")
    assert sm.article(page, "s")["fills"][1]["text"] == "a b\n    c"


def test_code_inside_a_list_item_splits_the_list(art):
    kinds = [s["kind"] for s in art["seq"]]
    assert kinds == ["h2", "p", "fill", "p", "h3", "li", "li", "fill", "li", "fill", "fill"]
    assert (
        "<ol><li><p>One</p></li><li><p>Two</p></li></ol><pre>CODEBLOCK-1</pre><ol><li><p>Three</p></li></ol>"
        in art["html"]
    )
    assert art["fills"][1] == {"text": "echo hi", "label": "Bash"}


def test_a_table_becomes_aligned_plain_text(art):
    assert art["fills"][2] == {"text": "A          Bee\n---------  ---\nlong cell  b", "label": "Plain Text"}


def test_a_block_without_a_language_is_plain_text(art):
    assert art["fills"][3] == {"text": "plain & simple", "label": "Plain Text"}


def test_code_and_tables_are_pasted_as_placeholders(art):
    assert [f"<pre>CODEBLOCK-{i}</pre>" in art["html"] for i in range(4)] == [True] * 4
    assert "return 1" not in art["html"]


def test_paste_html_keeps_only_link_targets(art):
    assert (
        '<p>Run <code>x</code> with <strong>bold <code>y</code> text</strong> and <a href="/a">a link</a>.</p>'
        in art["html"]
    )
    assert 'id="section"' not in art["html"]


def test_inline_formatting_compares_as_text(art):
    # Medium splits a <strong> around a <code> inside it; the text stays the same
    p = art["seq"][1]
    assert (p["links"], p["code"], p["strong"], p["em"]) == ("alink", "xy", "boldytext", "")


def test_metadata_and_topics(art):
    assert (art["title"], art["subtitle"], art["description"]) == (
        "A Title",
        "A subtitle.",
        "A description over two lines.",
    )
    assert art["tags"] == ["python", "testing"]
    assert art["topics"] == ["Python", "Software Testing", "Software Engineering", "Programming", "DevOps"]
    assert art["url"] == "https://philipptheserver.com/posts/a-slug/"


def test_infrastructure_posts_fill_with_devops_first():
    assert sm.topics(["ansible", "linux"]) == ["Ansible", "Linux", "DevOps", "Software Engineering", "Programming"]


def test_an_element_medium_cannot_hold_is_refused():
    with pytest.raises(ValueError, match="blockquote"):
        sm.article(PAGE.replace("<p>After.</p>", "<blockquote>q</blockquote>"), "s")


def test_every_post_tag_has_a_medium_topic():
    tags = set()
    for post in (REPO / "_posts").glob("*.md"):
        line = re.search(r"^tags: \[(.*)\]$", post.read_text(encoding="utf-8"), re.MULTILINE)
        tags |= {t.strip() for t in line.group(1).split(",")}
    assert tags - sm.MEDIUM_TOPIC.keys() == set()


def test_every_built_article_can_be_modelled():
    pages = sorted((REPO / "_site" / "posts").glob("*/index.html"))
    if not pages:
        pytest.skip("no built site; run scripts/verify.sh first")
    assert len(pages) == len(list((REPO / "_posts").glob("*.md")))
    for page in pages:
        html = page.read_text(encoding="utf-8")
        art = sm.article(html, page.parent.name)
        body = html[html.index('class="post-body prose"') :]
        assert len(art["fills"]) == body.count("<pre") + body.count("<table"), page.parent.name
        for fill in art["fills"]:
            assert not re.search(f"[{FS}{WJ}\u00a0\t]", fill["text"]), page.parent.name

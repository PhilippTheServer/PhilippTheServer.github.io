# Site redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make philipptheserver.com machine-readable first (corrected facts, richer
structured data) and then give it the look of the design handoff, keeping the pixel crew.

**Architecture:** The Jekyll site is restyled in place. Facts live in `_data/*.yml` and a
new `_work` collection; layouts and two stylesheets render them; `scripts/check_site.rb`
pins every promise. Two PRs: facts and structured data first (no visual change), then the
visual switch.

**Tech Stack:** Jekyll 4 (kramdown GFM, jekyll-feed, jekyll-sitemap), Ruby plugins in
`_plugins/`, plain CSS, two small vanilla scripts, html-proofer, `node --test`, uv/pytest
for the Medium tooling.

Spec: `docs/superpowers/specs/2026-10-01-site-redesign-design.md`.

## Global Constraints

- Machine paths never move: `/llms.txt`, `/llms-full.txt`, `/profile.json`, `/resume.json`, `/ai.txt`, `/humans.txt`, `/feed.xml`, `/sitemap.xml`, `/robots.txt`, `/.well-known/security.txt`.
- The Person JSON-LD is the FIRST `<script type="application/ld+json">` in every page and byte-identical to `/profile.json`.
- Article URLs stay `/posts/<slug>/`.
- Served article HTML keeps the Medium shape: `h1.page-title`, `p.page-subtitle`, `.post-body.prose` whose direct children are only `p`, `h2`–`h4`, `ul`/`ol`, `pre`, `div.table-scroll`; `.post-tags a` text is the bare tag; `<pre>` bare or `class="language-x" data-lang="x"`, `<br>` line breaks, no whitespace after `</pre>`.
- No request to another origin from any page: fonts, scripts, styles and images are served from the site.
- Career: System Administrator 2022-03-01 → 2023; Head of Administration and IT 2023 → 2026-09-06; CTO since 2026-09-06; all Nerd Force1 UG.
- open Skunkforce website: `https://skunkforce.org/`.
- Only public repositories are linked. Public today: `homelab-setup`, `daily-companion`, `gym-bro-tracking`, `ceph-cluster-on-a-budget` (all under `github.com/PhilippTheServer/`), and `github.com/OpenTaberna`.
- Pixel crew: `assets/js/pixel-crew.js` unchanged; `#pixel-bg` rule unchanged (z-index 5, pointer-events none, hidden below 700 px); home page only.
- Dark only. Colour tokens exactly as in the handoff README.
- No inline comments unless the why is non-obvious. Every change: issue → branch → PR (`Closes #N`) → squash merge → delete branch → close issue with comment.
- Verify with `./scripts/build-local.sh` (Docker build + checks + node test + Medium tooling).

## File map

| File | PR | Responsibility |
| --- | --- | --- |
| `_data/resume.yml` | 1 | Career, projects (public repo links only) |
| `_data/person.yml` | 1 | Person node: Skunkforce URL, `performerIn` the talk |
| `_includes/head.html` | 1, 2 | Title, `rel="me"`, og:image, JSON-LD includes; fonts, colour scheme |
| `_includes/structured-data-profile.html` | 1 | `WebSite` (home) and `ProfilePage` (home, about) |
| `llms.txt` | 1, 2 | Corrected prose; role and project lists rendered from `resume.yml`; Work in key pages |
| `about.md` | 1, 2 | Corrected career sentence; then the git log + prose |
| `scripts/check_site.rb` | 1, 2 | Every promise |
| `README.md` | 1, 2 | Machine files, verification, design |
| `assets/fonts/*` | 2 | Inter (variable, latin) and mononoki woff2 + OFL licences |
| `assets/css/site.css` | 2 | Tokens, shell, pages |
| `assets/css/prose.css` | 2 | Article body per the handoff's Markdown table |
| `assets/js/site.js` | 2 | Copy-feed toast, Konami toast, console coffee |
| `_layouts/default.html` | 2 | Header, main, footer |
| `_layouts/post.html` | 2 | Article |
| `_layouts/work.html` | 2 | Project detail |
| `_includes/post-list.html`, `_includes/tag-label.html`, `_includes/rss-row.html`, `_includes/profile-links.html`, `_includes/git-log.html`, `_includes/structured-data-work.html` | 2 | Components |
| `_plugins/tag_hue.rb` | 2 | `tag_hue` Liquid filter (handoff hash) |
| `_plugins/tag_pages.rb` | 2 | Tag pages render through `post-list.html` |
| `_data/career.yml` | 2 | Git log commits |
| `_work/*.md`, `work.md` | 2 | Projects and `/work/` |
| `index.md`, `posts.md` | 2 | Home, blog index |
| `_config.yml` | 2 | `work` collection |
| `llms-full.txt`, `humans.txt` | 2 | Work pages in full text; fonts note |
| `_includes/subscribe.html` | 2 | Deleted (replaced by `rss-row.html`) |

---

## PR 1 — Correct career facts and extend structured data

### Task 1: Issue and branch

- [ ] Open the issue in `PhilippTheServer/github-page` (check the remote name with `gh repo view --json nameWithOwner`): title "Correct career facts and extend structured data"; body states the expected behaviour (career as in Global Constraints everywhere; Skunkforce URL; talk; no private repo links; `WebSite` + `ProfilePage`; LinkedIn `rel="me"`; og:image; home title; checks that fail on regression).
- [ ] `git switch -c fix/<N>-career-facts-structured-data` from `main`.

### Task 2: Checks first (they must fail on today's site)

**Files:** Modify `scripts/check_site.rb`

- [ ] Replace section 9's career block with:

```ruby
CTO_SINCE = "2026-09-06"
HEAD_SINCE = "2023"
JOINED = "2022-03-01"
EMPLOYER = "Nerd Force1 UG"
CAREER = [
  { "position" => "CTO", "startDate" => CTO_SINCE, "endDate" => nil },
  { "position" => "Head of Administration and IT", "startDate" => HEAD_SINCE, "endDate" => CTO_SINCE },
  { "position" => "System Administrator", "startDate" => JOINED, "endDate" => HEAD_SINCE },
].freeze

if profile
  fail!("profile.json: jobTitle is #{profile['jobTitle'].inspect}, expected \"CTO\"") unless profile["jobTitle"] == "CTO"
end

if resume
  at_employer = Array(resume["work"]).select { |w| w["name"] == EMPLOYER }
  CAREER.each_with_index do |want, i|
    got = at_employer[i]
    if got.nil?
      fail!("resume.json: no #{want['position']} entry at #{EMPLOYER}")
      next
    end
    %w[position startDate endDate].each do |key|
      next if got[key] == want[key]
      fail!("resume.json: #{EMPLOYER} entry #{i} has #{key} #{got[key].inspect}, expected #{want[key].inspect}")
    end
  end
  fail!("resume.json: #{at_employer.length} entries at #{EMPLOYER}, expected #{CAREER.length}") if at_employer.length != CAREER.length
end
```

  and keep the `llms.txt` promotion-date check after it. Add, next to it:

```ruby
if llms_txt
  CAREER.each do |role|
    fail!("llms.txt: does not name the role #{role['position']}") unless llms_txt.include?(role["position"])
  end
  fail!("llms.txt: still says he joined as Head of Administration and IT") if llms_txt.match?(/joined[^.]*as Head of Administration/i)
end
```

- [ ] Extend check 1 so every machine path is non-empty:

```ruby
%w[llms.txt llms-full.txt profile.json resume.json ai.txt humans.txt robots.txt
   .well-known/security.txt feed.xml sitemap.xml].each do |f|
  path = File.join(SITE, f)
  fail!("#{f}: empty") if File.file?(path) && File.size(path).zero?
end
```

- [ ] Add the identity checks (section 6b, after the JSON-LD equality check):

```ruby
def ld_nodes(html) = html.scan(%r{<script type="application/ld\+json">(.*?)</script>}m).flatten.map { |b| JSON.parse(b) rescue nil }.compact

PERSON_ID = "https://#{DOMAIN}/#person"
if (home = read("index.html"))
  nodes = ld_nodes(home)
  site_node = nodes.find { |n| n["@type"] == "WebSite" }
  fail!("index.html: no WebSite structured data") if site_node.nil?
  if site_node
    fail!("index.html: WebSite name is #{site_node['name'].inspect}") unless site_node["name"] == "Philipp Lehmann"
    fail!("index.html: WebSite alternateName must be PhilippTheServer") unless site_node["alternateName"] == "PhilippTheServer"
  end
end
%w[index.html about/index.html].each do |page|
  html = read(page) or next
  profile_page = ld_nodes(html).find { |n| n["@type"] == "ProfilePage" }
  fail!("#{page}: no ProfilePage structured data") if profile_page.nil?
  fail!("#{page}: ProfilePage mainEntity must reference #{PERSON_ID}") if profile_page && profile_page.dig("mainEntity", "@id") != PERSON_ID
  ["https://github.com/PhilippTheServer", "https://orcid.org/#{ORCID}", "https://www.linkedin.com/in/philipp-lehmann-17995521b/"].each do |me|
    fail!("#{page}: no rel=\"me\" link to #{me}") unless html.include?(%(<link rel="me" href="#{me}">))
  end
  fail!("#{page}: no og:image") unless html.include?(%(<meta property="og:image"))
end
if (home = read("index.html"))
  title = home[%r{<title>(.*?)</title>}m, 1].to_s
  fail!("index.html: <title> #{title.inspect} does not lead with the name and role") unless title.start_with?("Philipp Lehmann · CTO")
end
if profile
  skunk = Array(profile["memberOf"]).find { |o| o["name"] == "open Skunkforce e.V." }
  fail!("profile.json: open Skunkforce's url must be https://skunkforce.org/") unless skunk && skunk["url"] == "https://skunkforce.org/"
  talk = profile["performerIn"]
  fail!("profile.json: performerIn must name the pkcds4k talk") unless talk.is_a?(Hash) && talk["name"].to_s.include?("pkcds4k")
end
```

- [ ] Add the public-repo check (section 8b):

```ruby
PUBLIC_REPOS = %w[homelab-setup daily-companion gym-bro-tracking ceph-cluster-on-a-budget].freeze
%w[index.html about/index.html llms.txt llms-full.txt profile.json resume.json].each do |f|
  body = read(f) or next
  body.scan(%r{github\.com/PhilippTheServer/([A-Za-z0-9._-]+)}).flatten.uniq.each do |repo|
    next if PUBLIC_REPOS.include?(repo)
    fail!("#{f}: links github.com/PhilippTheServer/#{repo}, which is not a public repository")
  end
end
```

- [ ] Add the projects-in-llms check:

```ruby
if resume && llms_txt
  Array(resume["projects"]).each do |p|
    fail!("llms.txt: does not list the project #{p['name']}") unless llms_txt.include?("**#{p['name']}**")
  end
end
```

- [ ] Run `./scripts/build-local.sh` → Expected: FAIL, listing the career, WebSite, ProfilePage, rel=me LinkedIn, og:image, title, Skunkforce, performerIn, private-repo and project-list failures.

### Task 3: Facts

**Files:** `_data/resume.yml`, `_data/person.yml`, `about.md`, `llms.txt`

- [ ] `resume.yml` work: CTO (unchanged); Head of Administration and IT `startDate: "2023"`, `endDate: "2026-09-06"` (keeps highlights); new System Administrator entry `startDate: "2022-03-01"`, `endDate: "2023"`, summary "Servers, the network, and whatever broke that day." Skunkforce `url: "https://skunkforce.org/"`.
- [ ] `resume.yml` projects = the Work list: OpenTaberna (`https://opentaberna.de`), APIC-Modmode (`https://apic-modmode.de`), Internal Operations Platform (no url), atlas (no url), daily (`https://github.com/PhilippTheServer/daily-companion`), gym-bro (`https://github.com/PhilippTheServer/gym-bro-tracking`), homelab (`https://github.com/PhilippTheServer/homelab-setup`). Drop the three private side repos.
- [ ] `person.yml`: Skunkforce `url: "https://skunkforce.org/"`; add

```yaml
performerIn:
  "@type": "Event"
  name: "pkcds4k data science conference"
  startDate: "2025-07"
  workPerformed:
    "@type": "CreativeWork"
    name: "Ceph Cluster on a Budget"
    inLanguage: "de"
    url: "https://github.com/PhilippTheServer/ceph-cluster-on-a-budget"
```

- [ ] `about.md` CTO role paragraph: "At Nerd Force1 since March 2022: system administrator first, Head of Administration and IT from 2023, CTO since September 2026." Skunkforce paragraph links `https://skunkforce.org/`.
- [ ] `llms.txt`: rewrite the CTO sentence and the "Notes for language models" career note to the confirmed history; Skunkforce sentence and profile link → skunkforce.org; add a "Talks" paragraph (pkcds4k, Jul 2025, "Ceph Cluster on a Budget", 12-minute talk in German, repo link); add a "## Roles" list rendered from `site.data.resume.work` (`- **{{ position }}**, {{ name }} ({{ startDate }} – {{ endDate | default: "present" }})`) and a "## Projects" list from `site.data.resume.projects` (`- **{{ name }}**: {{ description }}{% if url %} {{ url }}{% endif %}`).

### Task 4: Head and structured data

**Files:** `_includes/head.html`, create `_includes/structured-data-profile.html`, `index.md`

- [ ] `index.md` front matter: `head_title: "Philipp Lehmann · CTO & infrastructure engineer in Bochum"`; `<title>` uses `page.head_title` first.
- [ ] `head.html`: `<link rel="me" href="{{ site.author.linkedin }}">`; `og:image` and `twitter:image` = `site.data.person.image`; after the Person include, `{%- if page.url == '/' or page.url == '/about/' -%}{%- include structured-data-profile.html -%}{%- endif -%}`.
- [ ] `structured-data-profile.html`: on `/` a `WebSite` node (`@id` `/#website`, name, alternateName, url, inLanguage, publisher → Person `@id`), and on both pages a `ProfilePage` node (`@id` page URL, url, name, `mainEntity: {"@id": "…/#person"}`, `isPartOf: {"@id": "…/#website"}`, `dateModified` = `site.time`).
- [ ] Run `./scripts/build-local.sh` → Expected: `check_site.rb: all checks passed`, htmlproofer OK, node tests pass, Medium tooling pass.

### Task 5: Docs, commit, PR, merge

- [ ] README: machine-readable table gets the `WebSite`/`ProfilePage` nodes; the verification paragraph names the new checks.
- [ ] Commit spec + plan + changes; push; PR with `Closes #N`, the verify command and its output; wait for CI green; squash merge; delete branch; close issue with a comment describing the solution.
- [ ] After deploy: `curl -s https://philipptheserver.com/ | grep -c ProfilePage` → ≥ 1.

---

## PR 2 — Redesign the site to the handoff design

### Task 6: Issue, branch, fonts

- [ ] Issue "Redesign the site to the handoff design" (expected behaviour = the spec's Pages section; justification for the two font families recorded there).
- [ ] Branch `feat/<N>-redesign`.
- [ ] Download Inter (variable, latin subset, woff2) and mononoki Regular/Bold/Italic woff2 into `assets/fonts/`, plus `assets/fonts/Inter-OFL.txt` and `assets/fonts/mononoki-OFL.txt`.

### Task 7: Checks for the redesign (fail first)

**Files:** `scripts/check_site.rb`

- [ ] Delete check 15 (landing structure in `index.md`), the subscribe-callout checks, and the `[data-theme]` / `prefers-color-scheme` assertions in section 12.
- [ ] Add:

```ruby
# Home: who he is, in visible text.
if (home = read("index.html"))
  fail!("index.html: no <h1> with the name") unless home.match?(%r{<h1[^>]*>\s*Philipp Lehmann\s*</h1>})
  fail!("index.html: no role line") unless home.include?("CTO at Nerd Force1 UG")
  ["Nerd Force1", "Ruhr University Bochum", "OpenTaberna", "whoami"].each do |s|
    fail!("index.html: the whoami does not mention #{s}") unless home.include?(s)
  end
end

# The RSS row on /posts/.
if (writing = read("posts/index.html"))
  row = writing[%r{<div class="rss-row".*?</div>}m]
  fail!("posts/index.html: no RSS row") if row.nil?
  fail!("posts/index.html: the RSS row does not link the feed") if row && !row.include?(%(href="https://#{DOMAIN}/feed.xml"))
end

# Dark only.
fail!("site.css: not dark only (color-scheme: dark)") unless site_css.to_s.include?("color-scheme: dark")

# Every built page links the Impressum and loads nothing from another origin.
Dir.glob(File.join(SITE, "**", "*.html")).each do |path|
  rel = path.delete_prefix("#{SITE}/")
  html = File.read(path)
  next unless html.include?("<html")
  fail!("#{rel}: does not link the Impressum") unless html.include?(%(href="/impressum/"))
  html.scan(/<(?:script|img|iframe|source)[^>]+src="(https?:[^"]+)"/).flatten.each { |u| fail!("#{rel}: loads #{u} from another origin") }
  html.scan(/<link[^>]+rel="(?:stylesheet|preload|modulepreload|icon)"[^>]*href="(https?:[^"]+)"/).flatten.each { |u| fail!("#{rel}: loads #{u} from another origin") }
end
Dir.glob(File.join(SITE, "assets", "css", "*.css")).each do |path|
  File.read(path).scan(/url\(\s*['"]?(https?:[^'")]+)/).flatten.each { |u| fail!("#{File.basename(path)}: loads #{u} from another origin") }
end

# Work: every project has a page, a node, and an entry in resume.json.
WORK = Dir.glob(File.join(__dir__, "..", "_work", "*.md")).sort.map do |f|
  YAML.safe_load(File.read(f)[/\A---\n(.*?)\n---/m, 1]).merge("id" => File.basename(f, ".md"))
end
fail!("_work/: no projects") if WORK.empty?
if (works = read("work/index.html"))
  list = ld_nodes(works).find { |n| n["@type"] == "ItemList" }
  fail!("work/index.html: no ItemList structured data") if list.nil?
  WORK.each do |p|
    fail!("work/index.html: does not link /work/#{p['id']}/") unless works.include?(%(href="/work/#{p['id']}/"))
    named = Array(list&.dig("itemListElement")).any? { |e| e.dig("item", "name") == p["title"] }
    fail!("work/index.html: ItemList does not name #{p['title']}") unless named
  end
else
  fail!("work/index.html: missing")
end
WORK.each do |p|
  html = read("work/#{p['id']}/index.html")
  if html.nil?
    fail!("work/#{p['id']}/: missing")
    next
  end
  node = ld_nodes(html).find { |n| %w[CreativeWork SoftwareSourceCode].include?(n["@type"]) }
  fail!("work/#{p['id']}/: no project structured data") if node.nil?
  fail!("work/#{p['id']}/: creator must reference #{PERSON_ID}") if node && node.dig("creator", "@id") != PERSON_ID
  in_resume = Array(resume&.dig("projects")).any? { |r| r["name"] == p["title"] } ||
              Array(resume&.dig("work")).any? { |w| w["name"] == p["title"] }
  fail!("resume.json: does not carry the Work project #{p['title']}") unless in_resume
  repo = p["repo"].to_s[%r{github\.com/PhilippTheServer/([^/]+)}, 1]
  fail!("_work/#{p['id']}.md: repo #{repo} is not public") if repo && !PUBLIC_REPOS.include?(repo)
end

# The git log on /about/ tells the same career as resume.json.
CAREER_LOG = YAML.safe_load(File.read(File.join(__dir__, "..", "_data", "career.yml")))
CAREER.each do |role|
  entry = CAREER_LOG.find { |c| c["role"] == role["position"] }
  fail!("_data/career.yml: no commit for #{role['position']}") if entry.nil?
  fail!("_data/career.yml: #{role['position']} is dated #{entry['start'].inspect}, resume.json says #{role['startDate'].inspect}") if entry && entry["start"] != role["startDate"]
end
if (about = read("about/index.html"))
  fail!("about/index.html: the git log commits are not <details>") if about.scan(%(<details class="commit")).length != CAREER_LOG.length
  CAREER_LOG.each { |c| fail!("about/index.html: commit #{c['hash']} missing") unless about.include?(c["hash"]) }
end
```

- [ ] Extend the pixel-crew "nowhere else" list with `work/index.html`; extend the JSON-LD equality list and the canonical list with `work/index.html`.
- [ ] Run `./scripts/build-local.sh` → Expected: FAIL (no RSS row, no Work, no career.yml, not dark only).

### Task 8: Shell — tokens, fonts, header, footer, site.js

**Files:** `assets/css/site.css` (rewrite), `_layouts/default.html`, `_includes/head.html`, `assets/js/site.js`, `_includes/profile-links.html`

- [ ] `site.css`: `:root` with the handoff tokens verbatim, `color-scheme: dark`, `@font-face` for Inter (400–700 from the variable file) and mononoki (400, 700, italic) with `local()` first, base (`body` system stack 16/1.6, links `--red`, `::selection --hl`), `.skip-link`, the unchanged `#pixel-bg` rule, header (`.site-head`, `.brand`, `.brand-tile`, `.nav a[aria-current]` bordeaux pill), `.content` (760 px, 20 px gutters), `.section-title` (underlined 4 px `--line`, offset 7 px), footer (`.site-foot`), toast (`.toast`).
- [ ] `default.html`: header with `<a class="brand" href="/"><span class="brand-tile">P</span><span>PhilippTheServer</span></a>` and nav `blog` (`/posts/`, current on posts, post layout and tag pages), `work` (`/work/`, current on work and work layout), `about`, `github`; `main#content.content`; page heading for pages that set `title` but not `hide_title` and are not posts/home; footer `© {{ site.time | date: "%Y" }} Philipp Lehmann` + link line; `<script src="/assets/js/site.js" defer>`.
- [ ] `head.html`: `<meta name="color-scheme" content="dark">`, `<meta name="theme-color" content="#1e1e22">`, preload the Inter woff2.
- [ ] `site.js`: copy button `[data-copy]` → `navigator.clipboard.writeText` and toast; Konami → toast; console coffee.

### Task 9: Home, blog index, post, prose

**Files:** `index.md`, `posts.md`, `_includes/rss-row.html`, `_includes/post-list.html`, `_includes/tag-label.html`, `_plugins/tag_hue.rb`, `_plugins/tag_pages.rb`, `_layouts/post.html`, `assets/css/prose.css` (rewrite); delete `_includes/subscribe.html`

- [ ] `tag_hue.rb`: `def tag_hue(tag) = tag.to_s.each_char.reduce(0) { |h, c| (h * 31 + c.ord) % 360 }` as a Liquid filter.
- [ ] `tag-label.html`: `<a class="tag-label" href="/tags/{{ include.tag }}/" style="--h: {{ include.tag | tag_hue }}">{{ include.tag }}</a>`; CSS colours it with `oklch(0.55 0.12 var(--h) / .18)` / `oklch(0.7 0.12 var(--h) / .45)` / `oklch(0.85 0.1 var(--h))`.
- [ ] `post-list.html`: `<ol class="timeline">` of `<li class="timeline-entry">` with `<time>`, `<h2><a>`, `.entry-box` (description, clamp 2 lines) and `.post-tags` of tag labels; optional `image`.
- [ ] `tag_pages.rb`: `TagPage` content becomes `{% include post-list.html posts=page.posts %}` plus the "All topics · All writing" line; `TagIndex` keeps its list.
- [ ] `index.md`: `<h1 class="hero-name">Philipp Lehmann</h1>`, `<p class="hero-role">CTO at Nerd Force1 UG · infrastructure engineer · Bochum, Germany</p>`, `.greeting`, `.whoami` prompt and paragraph, `{% include profile-links.html %}`; keep `pixel_crew: true`, `head_title`, `description`, `nav_order: 1`, `hide_title: true`.
- [ ] `posts.md`: `title: Blog`, RSS row, tag chips (`everything` + the eight most-used tags, computed in Liquid from `site.tags`), `{% include post-list.html posts=site.posts %}`, topics and llms-full lines.
- [ ] `post.html`: `.post-crumbs` (`Posts ›` link, date pill, `.post-tags` labels), `h1.page-title`, `p.page-subtitle` summary box, `.post-body.prose`, `.post-foot` (byline, `$ mail` line, prev/next from `page.previous` / `page.next`), highlight.js block unchanged.
- [ ] `prose.css`: Inter 17.5/1.65/−0.005em; p/h1–h6 sizes and margins from the handoff table; inline code; `pre` card (`--card`, radius 12, `--shadow`, mononoki 14/1.7, 16 px padding, `overflow-x: auto`) with `pre[data-lang]::before` a sticky header bar (`--wash`, 12.5 px `--mute`, right-aligned language); hljs classes mapped to `--tk-kw`, `--tk-str`, `--tk-num`, `--tk-com`, rest `--ink`; tables, blockquote, lists (mononoki `--red` markers), task lists, hr `* * *`, images and captions, footnotes, kbd, dl.

### Task 10: Work

**Files:** `_config.yml`, `_work/*.md` (8), `work.md`, `_layouts/work.html`, `_includes/structured-data-work.html`, `llms-full.txt`, `llms.txt`

- [ ] `_config.yml`: `collections: { work: { output: true, permalink: /work/:name/ } }` and `defaults` giving `_work` the `work` layout.
- [ ] One file per project with front matter `title`, `order`, `group` (`main` | `side`), `year`, `role`, `status`, `stack`, `url`, `repo`, `photo` (alt text), `image` (optional), `description`, `highlights`, `posts` (slugs); body empty or short prose. Data from the handoff's `PROJECTS`, with daily rewritten (agent-operated health and diet system with an MCP server; repo `daily-companion`; post `daily-meal-tracking-shopping-automation-mcp`), gym-bro repo `gym-bro-tracking`, homelab repo `homelab-setup`, atlas no repo and post `atlas-agentic-ops`, Skunkforce url `https://skunkforce.org/`, OpenTaberna repo `https://github.com/OpenTaberna` and its three posts.
- [ ] `work.md` (`/work/`, `title: Works`): two grids of `.work-card` from `site.work | sort: "order"` by group.
- [ ] `work.html`: crumbs `Works › name · year`, description, `.work-facts` rows when non-empty, highlights, links, related posts, screenshots when `image` set; JSON-LD via `structured-data-work.html`.
- [ ] `structured-data-work.html`: `ItemList` on `/work/`; on a project page `SoftwareSourceCode` when `repo` is set (with `codeRepository`), else `CreativeWork`; `creator` → Person `@id`; `url`; `description`.
- [ ] `llms-full.txt`: include `site.work` after the pages; `llms.txt` key pages gain Work.

### Task 11: About

**Files:** `_data/career.yml`, `_includes/git-log.html`, `about.md`

- [ ] `career.yml`: the handoff's `LOG` with `hash`, `date`, `lane`, `type`, `msg`, `story`, optional `url`/`url_label`, `head`, `fork`, and `role` + `start` on the three Nerd Force1 entries (`start` = the resume `startDate`). The 2023 entry reads "Head of Administration and IT at Nerd Force1"; the Mar 2022 entry "sysadmin at Nerd Force1 (AI-Gruppe)"; the talk links the talk repo.
- [ ] `git-log.html`: one `<details class="commit">` per entry; `<summary>` holds the graph cell (CSS lines at 11 px red and 31 px green, dot at 6/26 px, SVG curve at the fork row) and the line (hash `--gityel`, type `--gitgreen` bold, message, link, `(HEAD -> main)`, date `--gitred`); the story in the body.
- [ ] `about.md`: `title: About`, `hide_title: true`; "Bio" section title, `$ git log --graph ~/life · click a commit for the story`, the include; then "What I actually do", "Education", "The stack, honestly" (facts list), "What's next" (handoff copy), "On the web" (`profile-links.html` in list form), and the paragraph pointing language models at `/llms.txt`.

### Task 12: Verify, look, docs, ship

- [ ] `./scripts/build-local.sh` → Expected: all checks pass, html-proofer clean, node tests pass, Medium tooling pass.
- [ ] Serve `_site` locally (`python3 -m http.server -d _site 4000`) and compare home, blog, post, work, work detail, about with the handoff screenshots at 909 px and 390 px; fix differences.
- [ ] README: design section (tokens, fonts, components), "Pixel repair crew" unchanged, verification paragraph rewritten for the new checks; `humans.txt` fonts line.
- [ ] Commit by area, push, PR `Closes #N` with the verify output, CI green, squash merge, delete branch, close issue with comment.
- [ ] After deploy: Pages workflow green; `curl` each URL in the spec's URL list returns 200; home shows the `<h1>`; `/work/` lists eight projects.

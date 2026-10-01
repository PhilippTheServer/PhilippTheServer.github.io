# Site redesign from the design handoff — design

Date: 2026-10-01

## Goal

Two things, in this order:

1. **Machine-readable first.** When someone searches for Philipp Lehmann, this site is the
   result, and a search engine's AI summary says who he is: Philipp Lehmann from Bochum,
   CTO at Nerd Force1 UG (AI-Gruppe), infrastructure engineer, open-source founder
   (OpenTaberna), IT Security student at Ruhr University Bochum. Every fact on the site,
   in its JSON-LD, `llms.txt`, `profile.json` and `resume.json` agrees, and every link a
   crawler follows resolves.
2. **Good-looking second.** The site takes the look of the design handoff
   ("Portfolio with blog and command palette", `design_handoff_philipptheserver_site`):
   dark only, first person, four sections — home, blog, work, about.

The pixel repair crew on the landing page stays exactly as it is.

## Decisions

| Question | Decision | Why |
| --- | --- | --- |
| Stack | Stay on Jekyll | The handoff suggests Astro only "if no stack exists yet". Jekyll keeps every check, the Medium tooling, the feed, `llms-full.txt` and the tag pages. |
| URLs | Unchanged; new `/work/` and `/work/<id>/` | Search indexes and Medium canonical links point at `/posts/<slug>/`. The nav item reads "blog" and links `/posts/`. |
| Machine paths | Unchanged: `/llms.txt`, `/llms-full.txt`, `/profile.json`, `/resume.json`, `/ai.txt`, `/humans.txt`, `/feed.xml`, `/sitemap.xml`, `/robots.txt`, `/.well-known/security.txt`, Person JSON-LD first in every `<head>` | Explicit requirement. A check fails if any goes missing. |
| Fonts | Inter and mononoki stored in `assets/fonts/` with their OFL licences | The handoff loads them from Google Fonts and jsDelivr. Google Fonts from Google's servers is a GDPR problem in Germany, and the site promises no third-party requests. |
| Theme | Dark only | The handoff is dark only. |
| Home | Name heading, role line, greeting, whoami, profile links, pixel crew | The handoff's home has no visible name; a name search needs one. |
| About | Git log first, then today's prose, restyled | The prose is what a summary is built from. |
| Career | Sysadmin from Mar 2022, Head of Administration and IT from 2023, CTO since 2026-09-06 — all at Nerd Force1 UG | Confirmed by the owner on 2026-10-01; replaces "Head of Administration and IT since 2022-03-01". |
| open Skunkforce | Website `https://skunkforce.org/` | Confirmed by the owner. |
| Talk | First talk at the pkcds4k data science conference, Jul 2025, slides and material in `github.com/PhilippTheServer/ceph-cluster-on-a-budget` | Confirmed by the owner. |
| daily | The agent-operated health and diet system with an MCP server (`daily-companion`, article of 2026-09-30) | The handoff's "MQTT digest of merged PRs" text and its `/posts/daily-pr-digest/` link were wrong. |
| Repo links | Only public repositories are linked | A private repo is a 404 for a reader and a crawler. atlas and APIC appear without a repo link until their public snapshots exist. |
| Code-block header | Language label drawn by CSS from `data-lang`; no filename | The served `<pre>` keeps the exact shape Medium's importer needs (#56). kramdown and Medium cannot carry a filename. |
| Tag filter | Tag chips link to `/tags/<tag>/` | Crawlable, no JavaScript. |
| Tag colours | Hue from a hash of the tag name, computed at build time | The handoff's formula, without client code. |

## Data: one place per fact

| Source | Feeds |
| --- | --- |
| `_data/person.yml` | Person JSON-LD in every page, `/profile.json` |
| `_data/resume.yml` | `/resume.json`; the role list in `llms.txt` |
| `_data/career.yml` (new) | The git log on `/about/` |
| `_work/<id>.md` (new collection) | `/work/`, `/work/<id>/`, their JSON-LD, the project list in `llms.txt` |

`check_site.rb` fails if `career.yml` and `resume.yml` disagree on a role, its employer or
its start, or if the Work projects and `resume.json`'s projects differ.

## Machine-readable layer

Kept as is: everything under "Machine paths" above, the Blog and BlogPosting JSON-LD, the
tag pages, Atom feed and sitemap.

Changed:

- **Facts** corrected everywhere they appear (career, open Skunkforce, the talk, the
  projects). `resume.json` stops linking private repositories.
- **`llms.txt`** keeps its hand-written prose; its list of roles and its list of projects
  are rendered from `resume.yml` and the `_work` collection, so they cannot drift.
- **`WebSite`** node on the home page: name "Philipp Lehmann", alternateName
  "PhilippTheServer", url. Search engines use it for the site name.
- **`ProfilePage`** node on `/` and `/about/`, `mainEntity` referencing the Person node by
  `@id`. This is the markup search engines document for a page about one person.
- **Work**: `/work/` carries an `ItemList` of the projects; each `/work/<id>/` carries its
  project as `CreativeWork` (or `SoftwareSourceCode` when it has a public repository) with
  `creator` referencing the Person node, `url`, and `codeRepository` when public.
- **`<head>`**: `rel="me"` for LinkedIn beside GitHub and ORCID; `og:image` is the avatar
  already in `person.yml`; the home page `<title>` becomes
  "Philipp Lehmann · CTO & infrastructure engineer in Bochum".
- The Person node stays the **first** JSON-LD block and byte-identical to `profile.json`.

## Pages

All pages share a sticky header (logo tile "P", "PhilippTheServer" → `/`; nav
`blog · work · about · github`, active item a bordeaux pill) and a footer
(`© 2026 Philipp Lehmann`; GitHub · ORCID · LinkedIn · mail · RSS · llms.txt · Impressum).
Content column 760 px, 20 px gutters. Tokens, type and spacing as in the handoff README.

- **Home `/`** — `<h1>` "Philipp Lehmann"; role line "CTO at Nerd Force1 UG ·
  infrastructure engineer · Bochum, Germany"; greeting pill; `philipp@theserver:~$ whoami`
  and the handoff's paragraph (Nerd Force1, AI-Gruppe, IT Security at RUB, OpenTaberna →
  `/work/opentaberna/`); profile link row (GitHub, ORCID, LinkedIn, mail, RSS, each
  `rel="me"` where it is a profile). Pixel crew unchanged. Nothing else.
- **Blog `/posts/`** — `<h1>` "Blog" in the underlined section style; RSS row
  (`$ subscribe`, feed URL as a link, copy button with toast "feed URL copied. welcome
  aboard."); tag chips ("everything" → `/posts/`, each tag → `/tags/<tag>/`); timeline list
  with date, title, description box with tag labels, dashed dividers; optional post
  `image` that takes no space when absent.
- **Post `/posts/<slug>/`** — `Posts › <date pill>` with tag labels right; title
  (`h1.page-title`); summary box (`p.page-subtitle`); body in Inter 17.5/1.65 with every
  Markdown construct styled per the handoff table; byline "Written by Philipp Lehmann";
  `philipp@theserver:~$ mail …` line; previous / next. The selectors the Medium tooling
  reads (`h1.page-title`, `p.page-subtitle`, `.post-body.prose`, `.post-tags a`) and the
  served `<pre>` shape do not change.
- **Work `/work/`** — "Works": OpenTaberna, APIC-Modmode, Internal Operations Platform,
  atlas; "Side quests": open Skunkforce, daily, gym-bro, homelab. Card: image if one
  exists (no box otherwise), name, one-liner. **`/work/<id>/`**: `Works › name · year`;
  description; ROLE / STACK / STATUS rows, each omitted when empty; highlights; links
  (website, public repo, related articles); screenshots if present.
- **About `/about/`** — "Bio": `$ git log --graph ~/life`, commits from `career.yml`, red
  main line, green study branch; each commit is a `<details>` so the story is in the HTML
  and opens without JavaScript. Then: what I actually do, education, the stack, what's
  next (handoff copy), on the web.
- **Tags, Impressum, style reference** — restyled, content unchanged.

`assets/js/site.js` (no dependencies): copy-feed toast (3.2 s), Konami toast "+30 lives.
spend them on the backlog.", coffee cup in the console. No other motion.

## Verification

`scripts/verify.sh` (CI on every push and PR) keeps every existing check that still
applies and changes these:

- **Replaced**: the old landing-page structure (hero, named sections, carded articles,
  fact list, subscribe callout) → home has `<h1>` "Philipp Lehmann", the role line, the
  whoami naming Nerd Force1, Ruhr University Bochum and OpenTaberna, and `rel="me"` links
  to GitHub, ORCID and LinkedIn. The subscribe callout → the RSS row on `/posts/` with the
  feed URL. The light/dark theme assertions → dark only (`color-scheme: dark`).
- **Career check (#7)** rewritten for the confirmed history: CTO since 2026-09-06; Head
  of Administration and IT 2023 → 2026-09-06; sysadmin 2022-03 → 2023; all Nerd Force1 UG.
- **New**: every machine path exists and is non-empty; `WebSite` and `ProfilePage` on `/`,
  `ProfilePage` on `/about/`, `ItemList` on `/work/` naming every project, one project
  node per `/work/<id>/`; `career.yml` ⇄ `resume.yml`; Work ⇄ `resume.json` projects;
  `llms.txt` names every role and project; every page (not a sample) links the Impressum;
  no page loads a script, stylesheet, font or image from another origin.
- Unchanged: Person JSON-LD = `profile.json`, ORCID everywhere, description/title lengths,
  OpenTaberna mentions, pixel crew (home only, top layer, click-through, none below
  700 px, Node behaviour test), Medium code-block shape, CNAME, post URLs, Medium tooling
  tests.

Visual check, by hand: the built site in a browser at desktop and phone width against the
handoff screenshots.

## Delivery

Following the global git workflow (issue → branch → PR → squash merge):

1. **Correct career facts and extend structured data** — no visual change; ships first.
   Carries this spec.
2. **Redesign the site to the handoff design** — the whole visual switch including
   `/work/`, in one PR, commits split by area. A half-switched site would look broken.

README ("Machine-readable files", "Pixel repair crew", "Verification") is updated in the
same PRs.

## Not in scope

- A command palette (in the handoff's name, not in its prototype or README).
- Screenshots for the Work pages and post images: the owner adds them to
  `assets/work/` and front matter later; the layout already handles their absence.
- Public snapshots of atlas and APIC: when they exist, their `repo` field is filled in.
- Updating ORCID and LinkedIn to the corrected career history — the owner's accounts.

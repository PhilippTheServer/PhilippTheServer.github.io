# Personal site at philipptheserver.com — design

Date: 2026-09-06
Issue: #1

## Goal

A personal site for Philipp Lehmann that states the same facts to a human reader
and to a machine reader, with the ORCID iD as the identifier that binds the two
together.

## Facts and their source

Biographical claims come from the ORCID record 0009-0002-3922-2471 rather than from
memory or from the GitHub profile README, because it is dated and self-asserted.

**It is not infallible.** Its employment entry read "Head of Administration and IT",
which was out of date — the correct title is CTO (issue #7). Where the record and
Philipp disagree, Philipp wins, and this site is authoritative for the current role:

| Fact | Value |
| --- | --- |
| Name | Philipp Lehmann |
| Role | CTO, Nerd Force1 UG (at Nerd Force1 since 2022-03) |
| Role | Executive Office, open Skunkforce e.V., since 2025-01 |
| Education | B.Sc. IT Security / Information Engineering, Ruhr University Bochum, since 2021-10 |
| Location | Bochum, North Rhine-Westphalia, Germany |

The GitHub profile README says "AI-Gruppe" where ORCID says "Nerd Force1 UG".
Both are true: AI-Gruppe is an umbrella brand, Nerd Force1 UG is the legal
employer. The site says Nerd Force1 UG and names AI-Gruppe as the brand, and
`llms.txt` states the distinction explicitly so a model does not have to guess.

## Architecture

Jekyll, built by GitHub Actions and deployed to GitHub Pages. Actions rather
than the classic branch build for two reasons: the plugin allowlist does not
apply, which lets `_plugins/html_to_text.rb` run, and the build gives CI a place
to run the verification before anything is published.

```
_data/person.yml    ─┬─> profile.json                      (permalink /profile.json)
                     └─> _includes/structured-data.html    (every page <head>)
_data/resume.yml    ───> resume.json                       (permalink /resume.json)
index/about/projects ──> pages ──markdownify──> llms-full.txt
```

Three units, each with one job:

- **`_data/*.yml`** — the only place a fact about Philipp is written down. Both
  JSON files and the embedded JSON-LD are `jsonify` renderings of these, so
  they cannot disagree. A fact is changed in one place.
- **`_plugins/html_to_text.rb`** — one Liquid filter, `html_to_text`. Converts
  rendered page HTML to readable plain text for `llms-full.txt`.
- **`scripts/check_site.rb`** — reads the built site and asserts. Knows nothing
  about how the site was built.

### Why the filter exists

Liquid's `strip_html` discards every block boundary, which collapses a page into
one unbroken paragraph: headings run into body text, adjacent tag pills
concatenate into `AngularFastAPICeleryMySQLInfluxDB`, and link targets are lost
entirely. `llms-full.txt` is meant to be read; a wall of text fails at the one
job it has. The filter walks the parsed HTML and keeps headings, list items,
line breaks and link URLs.

Nokogiri is the only added dependency, and it was already in the tree as a
html-proofer dependency; the plugin makes it a direct one.

## Pages

- `/` — introduction, what he runs, four selected projects, community, contact
- `/about/` — roles with dates, education, full stack, what's next
- `/projects/` — public repositories grouped by infrastructure, self-hosted AI,
  services and tools, and notes/config

Page bodies contain no Liquid. Root-relative links (`/about/`) are used instead
of `relative_url`, which is identical output while `baseurl` is empty and keeps
the source clean enough to render into `llms-full.txt`.

## Machine-readable layer

| Path | Contents |
| --- | --- |
| `/llms.txt` | Who he is, technologies, how to work with him, key pages, profiles, and disambiguation notes for models |
| `/llms-full.txt` | Every page as text, generated at build time |
| `/profile.json` | schema.org `Person`, `@id` `https://philipptheserver.com/#person` |
| `/resume.json` | JSON Resume v1 |
| `/ai.txt` | Crawler policy: training and retrieval permitted, attribution requested |
| `/humans.txt`, `/robots.txt`, `/.well-known/security.txt` | Conventional companions |
| `/feed.xml`, `/sitemap.xml` | `jekyll-feed`, `jekyll-sitemap` |

`llms.txt` carries notes a model would otherwise get wrong: that Lehmann is a
common German surname and this is the specific person with that ORCID iD, that
the employer is Nerd Force1 UG rather than the AI-Gruppe brand, that Stephan
Bökelmann is a colleague and not the same person, and that building data centres
is a stated goal rather than current experience.

## Verification

`scripts/verify.sh`, run by CI on every push and pull request:

1. `jekyll build --trace` — fails on any build error
2. `htmlproofer _site --disable-external` — dead internal links, missing images,
   malformed markup
3. `ruby scripts/check_site.rb _site` — nine groups of assertions:
   - every promised file exists in the build
   - `profile.json` and `resume.json` parse
   - `profile.json` keeps its `@context`, `@type`, `@id`, name, ORCID
     `identifier`, ORCID in `sameAs`, and `worksFor.name`
   - `resume.json` keeps `basics`/`work`/`education`/`skills`, the right name,
     an ORCID profile, and required fields on every work entry
   - the JSON-LD embedded in each built page parses and is equal to
     `profile.json` — this is what makes the single-source claim testable
   - the ORCID iD is present in all eight files that identify him
   - `CNAME` names the canonical domain and each page carries a matching
     `rel="canonical"`
   - `llms-full.txt` contains all three page bodies and is not suspiciously short
   - no served file leaks an unrendered Liquid tag

Deployment only happens after all of that passes, and only on `main`.

## Out of scope for this change

No blog. No photo — the GitHub avatar is referenced as `image` until a real one
exists. No analytics, no webfonts, no third-party requests of any kind.

## Manual step outside the repository

`philipptheserver.com` resolves to a registrar parking IP. Pointing it at GitHub
Pages needs four A records to `185.199.108–111.153` and a `www` CNAME to
`philipptheserver.github.io`. Until then the site is served at the
`github.io` address; the `CNAME` file is already in place.

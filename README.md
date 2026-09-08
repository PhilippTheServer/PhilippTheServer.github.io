# philipptheserver.com

Personal site of Philipp Lehmann — infrastructure engineer, Bochum.
Built with Jekyll, deployed to GitHub Pages from `main`.

Canonical URL: <https://philipptheserver.com>

## Local development

```bash
./scripts/serve.sh        # http://localhost:4000, live reload
```

The script runs Jekyll in Docker, so no local Ruby install is needed.

## Verification

```bash
./scripts/verify.sh       # builds the site, then checks it
```

`verify.sh` is what CI runs on every push and pull request. It fails if the
build breaks, if an internal link or image is dead, if `profile.json` or
`resume.json` stop being valid JSON, if the embedded JSON-LD drifts away from
`profile.json`, if the ORCID iD disappears from any file that must carry it,
if the landing page loses the structure that makes it readable (hero, named
sections, carded articles, fact list — see `check_site.rb`), or if `CNAME`
stops naming the canonical domain.

## Machine-readable files

| Path | What it is |
| --- | --- |
| `/llms.txt` | Summary of who I am and what is on this site, for language models |
| `/llms-full.txt` | Full text of every page, in one file |
| `/profile.json` | schema.org `Person` JSON-LD — also embedded in every page's `<head>` |
| `/resume.json` | [JSON Resume](https://jsonresume.org) v1 |
| `/ai.txt` | Crawler and training policy |
| `/humans.txt` | The people and tools behind the site |
| `/feed.xml` | Atom feed |
| `/sitemap.xml` | Sitemap |
| `/impressum/` | Legal notice (Angaben gemäß § 5 DDG), in German |

`profile.json`, `resume.json` and the embedded JSON-LD are all generated from
`_data/person.yml` and `_data/resume.yml`, so they cannot disagree.

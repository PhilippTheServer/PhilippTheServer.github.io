#!/usr/bin/env ruby
# Structural checks on the built site. Run by scripts/verify.sh and by CI.
# Exits non-zero, listing every failure, if the site stops holding together.
#
# A few checks read the SOURCE (index.md, the article bodies in _posts) rather
# than the build: a build that merely succeeds cannot tell a page that holds
# together from one that does not, and some regressions are invisible in the
# rendered output. Those checks live next to the rendered-HTML checks they
# complement, and they run in the same pass.

require "cgi"
require "json"
require "yaml"

SITE   = ARGV[0] || "_site"
ORCID  = "0009-0002-3922-2471"
DOMAIN = "philipptheserver.com"

# Every article the site promises, read from the plan rather than globbed from the build.
# The plan is the deliberate list: an article that disappears from _posts without also
# leaving docs/blog-plan.json is a mistake, and globbing the build would hide it.
PLAN = JSON.parse(File.read(File.join(__dir__, "..", "docs", "blog-plan.json")))
ARTICLES = PLAN.fetch("scheduled").map { |a| a.fetch("slug") }.freeze
VOCABULARY = PLAN.fetch("vocabulary").freeze

# Addresses an article may legitimately contain: documentation ranges (RFC 5737), private
# ranges, loopback, and GitHub Pages' published anycast set. Anything else is a real
# address, and a real address in an article written from private company repositories is
# the leak this check exists to catch. The rule is a pattern rather than a denylist,
# because a denylist of the things you must not publish is itself a publication.
ALLOWED_IPV4 = /\A(
  192\.0\.2\.\d+ | 198\.51\.100\.\d+ | 203\.0\.113\.\d+ |
  10\.\d+\.\d+\.\d+ | 192\.168\.\d+\.\d+ | 172\.(1[6-9]|2\d|3[01])\.\d+\.\d+ |
  127\.\d+\.\d+\.\d+ | 0\.0\.0\.0 | 255\.255\.255\.\d+ |
  185\.199\.10[89]\.153 | 185\.199\.11[01]\.153 |
  1\.1\.1\.1 | 1\.0\.0\.1 | 8\.8\.8\.8 | 8\.8\.4\.4 | 9\.9\.9\.9 |
  208\.67\.22[02]\.22[02] |
  100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+ |
  224\.\d+\.\d+\.\d+ | 239\.\d+\.\d+\.\d+ |
  \d+\.\d+\.\d+\.\d+\/\d+
)\z/x.freeze

@failures = []

def fail!(msg) = @failures << msg

def read(path)
  full = File.join(SITE, path)
  return nil unless File.file?(full)
  File.read(full)
end

def parse_json(path)
  raw = read(path)
  return fail!("#{path}: missing from the built site") && nil if raw.nil?
  JSON.parse(raw)
rescue JSON::ParserError => e
  fail!("#{path}: not valid JSON — #{e.message.lines.first.strip}")
  nil
end

# 1. Every file the site promises must exist.
(%w[
  index.html about/index.html impressum/index.html posts/index.html
  llms.txt llms-full.txt profile.json resume.json
  ai.txt humans.txt robots.txt .well-known/security.txt
  feed.xml sitemap.xml favicon.svg assets/css/site.css CNAME
] + ARTICLES.map { |a| "posts/#{a}/index.html" }).each do |f|
  fail!("#{f}: missing from the built site") unless File.file?(File.join(SITE, f))
end

# The files written for machines live at fixed paths that crawlers and models have already
# learned (#72). A path that still exists but renders empty is as gone as a missing one.
%w[llms.txt llms-full.txt profile.json resume.json ai.txt humans.txt robots.txt
   .well-known/security.txt feed.xml sitemap.xml].each do |f|
  path = File.join(SITE, f)
  fail!("#{f}: empty") if File.file?(path) && File.size(path).zero?
end

# The repository listing was replaced by the articles; it must not come back.
fail!("projects/index.html: the repo list should be gone") if File.file?(File.join(SITE, "projects/index.html"))

# 2. The Impressum. A site published from Germany must name who is responsible for its
#     content, and the link must live where a reader actually looks: the footer, on every
#     page. The page itself is German, because the obligation is German — the rest of the
#     site can stay English.
impressum = read("impressum/index.html")
if impressum.nil?
  fail!("impressum/index.html: the Impressum page is missing")
else
  fail!("impressum/index.html: does not name the responsible person") unless impressum.include?("Philipp Lehmann")
  fail!("impressum/index.html: does not carry the contact address") unless impressum.include?(%(href="mailto:philipp.lehmann@gruppe.ai"))
  fail!("impressum/index.html: does not cite the legal basis") unless impressum.include?("§ 5 DDG")
end

# The footer is part of the default layout, so every page carries it. Check a spread of
# pages rather than one: a link removed from the layout would drop from all of them at
# once, and a link added by hand to a single page would not reach the rest.
%w[index.html about/index.html impressum/index.html posts/index.html].each do |page|
  html = read(page) or next
  fail!("#{page}: the footer does not link the Impressum") unless html.include?(%(href="/impressum/"))
end

# An article nobody can find is an article that is not published. Each must be listed on
# the index, named in llms.txt, carried in full by llms-full.txt, and in the feed.
ARTICLES.each do |slug|
  url = "/posts/#{slug}/"
  index = read("posts/index.html")
  fail!("posts/index.html: does not link #{url}") if index && !index.include?(%(href="#{url}"))

  llms = read("llms.txt")
  fail!("llms.txt: does not list #{url}") if llms && !llms.include?("https://#{DOMAIN}#{url}")


  article = read("posts/#{slug}/index.html")
  next if article.nil?

  headings = article.scan(%r{<h1 class="page-title"[^>]*>(.*?)</h1>}m).flatten
  fail!("posts/#{slug}/: has no title") if headings.empty?
  # Both layouts used to render the heading, so every article showed its title and
  # subtitle twice. One heading, exactly.
  fail!("posts/#{slug}/: renders its title #{headings.length} times") if headings.length > 1
  title = headings.first&.strip

  full = read("llms-full.txt")
  if full && title && !full.include?(title)
    fail!("llms-full.txt: does not carry the text of #{url}")
  end
end

# 3. The two JSON files must parse.
profile = parse_json("profile.json")
resume  = parse_json("resume.json")

# 4. profile.json must stay a usable schema.org Person.
if profile
  {
    "@context" => "https://schema.org",
    "@type"    => "Person",
    "@id"      => "https://#{DOMAIN}/#person",
    "name"     => "Philipp Lehmann",
  }.each do |key, want|
    got = profile[key]
    fail!("profile.json: #{key} is #{got.inspect}, expected #{want.inspect}") unless got == want
  end

  %w[url email jobTitle description worksFor affiliation sameAs knowsAbout identifier].each do |key|
    fail!("profile.json: #{key} is missing or empty") if profile[key].nil? || profile[key] == ""
  end

  ident = profile["identifier"]
  unless ident.is_a?(Hash) && ident["value"] == "https://orcid.org/#{ORCID}"
    fail!("profile.json: identifier must carry the ORCID iD https://orcid.org/#{ORCID}")
  end

  unless Array(profile["sameAs"]).include?("https://orcid.org/#{ORCID}")
    fail!("profile.json: sameAs must list the ORCID iD")
  end

  employer = profile.dig("worksFor", "name")
  fail!("profile.json: worksFor.name is #{employer.inspect}") unless employer == "Nerd Force1 UG"
end

# 5. resume.json must stay valid JSON Resume.
if resume
  %w[basics work education skills].each do |key|
    fail!("resume.json: #{key} is missing or empty") if resume[key].nil? || resume[key].empty?
  end
  fail!("resume.json: basics.name is wrong") unless resume.dig("basics", "name") == "Philipp Lehmann"

  orcid_profile = Array(resume.dig("basics", "profiles"))
                  .find { |p| p["url"].to_s.include?(ORCID) }
  fail!("resume.json: basics.profiles must include the ORCID iD") if orcid_profile.nil?

  resume["work"].each_with_index do |job, i|
    %w[name position startDate].each do |key|
      fail!("resume.json: work[#{i}].#{key} is missing") if job[key].to_s.empty?
    end
  end
end

# 5b. OpenTaberna, which he founded with Malte Kottmann (#41). Founder, not
#     co-founder: that is how the two of them describe it, and the word is checked
#     everywhere the role is stated.
if profile
  taberna = Array(profile["memberOf"]).find { |o| o["name"] == "OpenTaberna" }
  if taberna.nil?
    fail!("profile.json: memberOf does not list OpenTaberna")
  elsif Array(taberna["founder"]).none? { |f| f["@id"] == "https://#{DOMAIN}/#person" }
    fail!("profile.json: OpenTaberna does not name him as a founder")
  end
end
if resume
  unless Array(resume["work"]).any? { |w| w["name"] == "OpenTaberna" && w["position"] == "Founder" }
    fail!("resume.json: work does not list OpenTaberna with the position Founder")
  end
end
%w[index.html about/index.html llms.txt profile.json resume.json].each do |f|
  body = read(f) or next
  fail!("#{f}: says co-founder; he and Malte Kottmann are founders of OpenTaberna") if body.downcase.match?(/co-?found/)
end

# 6. The JSON-LD embedded in each page must be byte-for-byte the same object as
#    profile.json — the page and the file can never claim different things.
(%w[index.html about/index.html posts/index.html work/index.html] +
 ARTICLES.map { |a| "posts/#{a}/index.html" }).each do |page|
  html = read(page) or next
  block = html[%r{<script type="application/ld\+json">(.*?)</script>}m, 1]
  if block.nil?
    fail!("#{page}: no JSON-LD block in the page")
    next
  end
  begin
    embedded = JSON.parse(block)
    fail!("#{page}: embedded JSON-LD has drifted from profile.json") if profile && embedded != profile
  rescue JSON::ParserError => e
    fail!("#{page}: embedded JSON-LD is not valid JSON — #{e.message.lines.first.strip}")
  end
end

# 6b. What a search engine needs to answer "who is this" from the home page (#72): a
#     WebSite node for the site name, a ProfilePage whose subject is the Person node, the
#     profiles that are his as rel="me", a preview image, and a title that leads with the
#     name and role rather than a tagline.
def ld_nodes(html)
  html.scan(%r{<script type="application/ld\+json">(.*?)</script>}m).flatten
      .map { |b| JSON.parse(b) rescue nil }.compact
end

PERSON_ID = "https://#{DOMAIN}/#person"
PROFILES = [
  "https://github.com/PhilippTheServer",
  "https://orcid.org/#{ORCID}",
  "https://www.linkedin.com/in/philipp-lehmann-17995521b/",
].freeze

if (home = read("index.html"))
  site_node = ld_nodes(home).find { |n| n["@type"] == "WebSite" }
  if site_node.nil?
    fail!("index.html: no WebSite structured data")
  else
    fail!("index.html: WebSite name is #{site_node['name'].inspect}") unless site_node["name"] == "Philipp Lehmann"
    fail!("index.html: WebSite alternateName must be PhilippTheServer") unless site_node["alternateName"] == "PhilippTheServer"
  end
  title = home[%r{<title>(.*?)</title>}m, 1].to_s
  fail!("index.html: <title> #{title.inspect} does not lead with the name and role") unless title.start_with?("Philipp Lehmann · CTO")
end

%w[index.html about/index.html].each do |page|
  html = read(page) or next
  profile_page = ld_nodes(html).find { |n| n["@type"] == "ProfilePage" }
  if profile_page.nil?
    fail!("#{page}: no ProfilePage structured data")
  elsif profile_page.dig("mainEntity", "@id") != PERSON_ID
    fail!("#{page}: ProfilePage mainEntity must reference #{PERSON_ID}")
  end
  PROFILES.each do |me|
    fail!("#{page}: no rel=\"me\" link to #{me}") unless html.include?(%(<link rel="me" href="#{me}">))
  end
  fail!("#{page}: no og:image") unless html.include?(%(<meta property="og:image"))
end

if profile
  skunk = Array(profile["memberOf"]).find { |o| o["name"] == "open Skunkforce e.V." }
  fail!("profile.json: open Skunkforce's url must be https://skunkforce.org/") unless skunk && skunk["url"] == "https://skunkforce.org/"
  talk = profile["performerIn"]
  fail!("profile.json: performerIn must name the pkcds4k talk") unless talk.is_a?(Hash) && talk["name"].to_s.include?("pkcds4k")
end

# 6c. A link to a private repository is a 404 for every reader and crawler that follows
#     it (#72). These are the public ones; a repository made public is added here on
#     purpose, which is the point.
PUBLIC_REPOS = %w[homelab-setup daily-companion gym-bro-tracking ceph-cluster-on-a-budget].freeze
%w[index.html about/index.html llms.txt llms-full.txt profile.json resume.json].each do |f|
  body = read(f) or next
  body.scan(%r{github\.com/PhilippTheServer/([A-Za-z0-9._-]+)}).flatten.uniq.each do |repo|
    next if PUBLIC_REPOS.include?(repo)
    fail!("#{f}: links github.com/PhilippTheServer/#{repo}, which is not a public repository")
  end
end

# 7. The ORCID iD is what binds every one of these files to one person.
%w[
  profile.json resume.json llms.txt llms-full.txt
  ai.txt humans.txt index.html about/index.html
].each do |f|
  body = read(f) or next
  fail!("#{f}: the ORCID iD #{ORCID} is missing") unless body.include?(ORCID)
end

# 8. Retired projects must not reappear. neteye was dropped from the site deliberately
#    (issue #3); it lives on in the GitHub account but is not presented as current work.
#    The merge digest was dropped from Work (#76); its article stays, the project does not.
#    Every generated file derives from the pages, so one stray card would put it back in
#    resume.json, llms-full.txt and the JSON-LD at once.
RETIRED = ["neteye", "merge digest"].freeze

fail!("work/merge-digest/index.html: the retired merge digest has a page again") if File.file?(File.join(SITE, "work/merge-digest/index.html"))

RETIRED.each do |name|
  %w[
    index.html about/index.html projects/index.html work/index.html
    llms.txt llms-full.txt profile.json resume.json
  ].each do |f|
    body = read(f) or next
    fail!("#{f}: mentions retired project #{name.inspect}") if body.downcase.include?(name)
  end
end

# 9. The career at Nerd Force1, which I got wrong twice from sources that looked
#    authoritative (issues #7, #72). He joined on 2022-03-01 as system administrator, was
#    Head of Administration and IT from 2023, and CTO since 2026-09-06. One entry per
#    title, newest first: an entry carrying a newer title and an older start date would
#    claim he held that title for longer than he did.
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
  fail!("resume.json: work[0].position is #{resume.dig('work', 0, 'position').inspect}, expected \"CTO\"") unless resume.dig("work", 0, "position") == "CTO"
  at_employer = Array(resume["work"]).select { |w| w["name"] == EMPLOYER }
  if at_employer.length != CAREER.length
    fail!("resume.json: #{at_employer.length} entries at #{EMPLOYER}, expected #{CAREER.length}")
  end
  CAREER.each_with_index do |want, i|
    got = at_employer[i] or next
    %w[position startDate endDate].each do |key|
      next if got[key] == want[key]
      fail!("resume.json: #{EMPLOYER} entry #{i} has #{key} #{got[key].inspect}, expected #{want[key].inspect}")
    end
  end
end

#    He administers Bind9 and NetBird, not a plain WireGuard deployment. NetBird is built
#    on WireGuard, so the article about the overlay mesh names it correctly and is
#    excluded here — but no surface describing what he RUNS may list it.
#    These describe what he runs. WireGuard has no business on any of them.
%w[index.html about/index.html profile.json resume.json].each do |f|
  body = read(f) or next
  fail!("#{f}: lists WireGuard as something he administers; he runs NetBird") if body.include?("WireGuard")
end

#    llms.txt is the exception, because it carries the note telling a model the difference
#    — and that note has to name WireGuard to be worth anything. So the rule there is not
#    "never mention it" but "never mention it without NetBird in the same breath": a line
#    naming WireGuard alone is the mistake, a line drawing the distinction is the fix.
#    The promotion date is the one fact the ORCID note exists to carry, since the record
#    itself dates the title from the start of his employment.
llms_txt = read("llms.txt")
if llms_txt && !llms_txt.include?(CTO_SINCE)
  fail!("llms.txt: does not name the promotion date #{CTO_SINCE} — the one thing the ORCID note is for")
end
if llms_txt
  CAREER.each do |role|
    fail!("llms.txt: does not name the role #{role['position']}") unless llms_txt.include?(role["position"])
  end
  fail!("llms.txt: still says he joined as Head of Administration and IT") if llms_txt.match?(/joined[^.]*\bas Head of Administration/i)
  Array(resume&.dig("projects")).each do |p|
    fail!("llms.txt: does not list the project #{p['name']}") unless llms_txt.include?("**#{p['name']}**")
  end
end

llms = read("llms.txt")
llms&.each_line&.with_index(1) do |line, n|
  next unless line.include?("WireGuard")
  next if line.include?("NetBird")
  # An entry in the generated writing list is an article title, not a claim about the
  # estate. The rule is about the prose that describes what he administers.
  next if line.start_with?("- [") && line.include?("/posts/")
  fail!("llms.txt:#{n}: names WireGuard without NetBird beside it")
end

# 10. Article structured data. Descriptive titles and a sitemap get a page crawled;
#    this is what lets a crawler know the page IS an article — headline, date, author,
#    keywords, language. Its absence was the largest indexing gap the site had.
ARTICLES.each do |slug|
  html = read("posts/#{slug}/index.html") or next
  blocks = html.scan(%r{<script type="application/ld\+json">(.*?)</script>}m).flatten

  posting = blocks.map { |b| JSON.parse(b) rescue nil }.compact
                  .find { |d| d["@type"] == "BlogPosting" }
  if posting.nil?
    fail!("posts/#{slug}/: no BlogPosting structured data")
    next
  end

  title = html[%r{<h1 class="page-title"[^>]*>(.*?)</h1>}m, 1]&.strip
  if title && posting["headline"] != title
    fail!("posts/#{slug}/: BlogPosting headline #{posting['headline'].inspect} != page title #{title.inspect}")
  end

  %w[datePublished description url inLanguage].each do |key|
    fail!("posts/#{slug}/: BlogPosting is missing #{key}") if posting[key].to_s.empty?
  end

  # The author must be a REFERENCE to the Person node, never an inlined copy — a copy is a
  # second place the same facts live, and it drifts exactly like the job title did.
  unless posting.dig("author", "@id") == "https://#{DOMAIN}/#person"
    fail!("posts/#{slug}/: BlogPosting author must reference the Person node by @id, not inline it")
  end
  fail!("posts/#{slug}/: BlogPosting author must not inline a name") if posting.dig("author", "name")

  # Google cuts the snippet at about 160 characters. A longer description hides whatever
  # the searcher typed past the cut, which is how an article ranks for an error message
  # and still collects no clicks.
  meta = CGI.unescapeHTML(html[/<meta name="description" content="([^"]*)"/, 1].to_s)
  unless (70..160).cover?(meta.length)
    fail!("posts/#{slug}/: meta description is #{meta.length} characters, must be 70–160")
  end

  words = posting["wordCount"].to_i
  fail!("posts/#{slug}/: wordCount is #{words}, which cannot be right") if words < 200

  # Every article carries the four-part shape. It is the thing that makes them articles
  # rather than notes, and it is invisible in a build that merely succeeds.
  ["The problem", "Working through it", "The solution", "Conclusion"].each do |heading|
    unless html.match?(%r{<h2[^>]*>\s*#{Regexp.escape(heading)}\s*</h2>}i)
      fail!("posts/#{slug}/: missing the '#{heading}' section")
    end
  end

  # And at least one code example, because an article claiming to be reconstructable
  # without one is not.
  blocks_of_code = html.scan(%r{<pre[ >]}).length
  fail!("posts/#{slug}/: has no code example") if blocks_of_code.zero?

  # Tags must come from the closed vocabulary and must resolve to a real tag page.
  Array(posting["keywords"].to_s.split(", ")).each do |tag|
    next if tag.empty?
    fail!("posts/#{slug}/: tag #{tag.inspect} is not in the vocabulary") unless VOCABULARY.include?(tag)
    unless File.file?(File.join(SITE, "tags", tag, "index.html"))
      fail!("posts/#{slug}/: tag #{tag.inspect} has no page at /tags/#{tag}/")
    end
  end

  # No real network address may appear in an article. See ALLOWED_IPV4 above.
  html.scan(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:\/\d{1,2})?\b/).uniq.each do |addr|
    next if addr.match?(ALLOWED_IPV4)
    fail!("posts/#{slug}/: contains the address #{addr} — articles must not carry real addresses")
  end
end

# Search results cut a title at about 60 characters. The archive predates this rule
# (76 of the first 116 titles are longer) and keeps its titles; every article from
# 117 on must fit, so the words a searcher reads are the ones the title leads with.
PLAN.fetch("scheduled").select { |a| a.fetch("n") >= 117 }.each do |a|
  if a.fetch("title").length > 60
    fail!("posts/#{a['slug']}/: title is #{a['title'].length} characters, must be 60 or fewer")
  end
end

# The OpenTaberna articles name the project as an entity in their BlogPosting, so a
# search engine can connect the article to the shop rather than to a word in it (#41).
%w[opentaberna-headless-open-source-shop opentaberna-order-processing-first
   opentaberna-storefront-against-the-api].each do |slug|
  html = read("posts/#{slug}/index.html") or next
  posting = html.scan(%r{<script type="application/ld\+json">(.*?)</script>}m).flatten
                .map { |b| JSON.parse(b) rescue nil }.compact
                .find { |d| d["@type"] == "BlogPosting" }
  named = Array(posting&.dig("mentions")).any? { |m| m["url"] == "https://opentaberna.de" }
  fail!("posts/#{slug}/: BlogPosting does not mention OpenTaberna") unless named
end

# daily is operated mostly by the self-hosted Qwen on atlas, not only by Claude (#51).
if (daily = read("posts/daily-meal-tracking-shopping-automation-mcp/index.html"))
  unless daily.include?("Qwen") && daily.include?('href="/posts/atlas-agentic-ops/"')
    fail!("posts/daily-meal-tracking-shopping-automation-mcp/: must name the self-hosted Qwen on atlas and link its article")
  end
end

# The feed carries the most recent articles, not the archive — that is what a feed is for,
# and jekyll-feed caps it at `posts_limit`. Assert the newest ones are in it and that the
# cap is doing what it says, rather than demanding every article.
feed = read("feed.xml")
if feed.nil?
  fail!("feed.xml: missing")
else
  newest = PLAN.fetch("scheduled").sort_by { |a| a.fetch("date") }.reverse.first(20)
  newest.each do |a|
    unless feed.include?("https://#{DOMAIN}/posts/#{a['slug']}/")
      fail!("feed.xml: does not carry the recent article /posts/#{a['slug']}/")
    end
  end
end

# A feed nobody can find is a feed nobody reads. The link elements and the footer
# entry are for software; a reader who does not already know what feed.xml is needs
# the URL in front of them (issues #33, #35), at the top of the blog (#37). Since the
# redesign (#74) that is the RSS row: the URL as a link and a button that copies it.
writing = read("posts/index.html")
if writing
  row = writing[%r{<div class="rss-row".*?</div>}m]
  if row.nil?
    fail!("posts/index.html: no RSS row")
  else
    fail!("posts/index.html: the RSS row does not link the feed") unless row.include?(%(href="https://#{DOMAIN}/feed.xml"))
    fail!("posts/index.html: the RSS row has no copy button") unless row.include?(%(data-copy="https://#{DOMAIN}/feed.xml"))
    fail!("posts/index.html: the RSS row does not come before the posts") if writing.index("rss-row") > writing.index(%(class="timeline")).to_i
  end
end

# The pixel repair crew belongs to the landing page only (issue #43): it is the first
# impression, and an animation behind a long article would compete with the text.
# The layout renders it behind a front-matter switch, so a switch set on the wrong
# page, or lost from index.md, only shows in the rendered output.
crew_tag = %(<canvas id="pixel-bg")
crew_js  = %(src="/assets/js/pixel-crew.js")
if (home = read("index.html"))
  fail!("index.html: the pixel crew canvas is missing") unless home.include?(crew_tag)
  fail!("index.html: the pixel crew script is not loaded") unless home.include?(crew_js)
end
(["about/index.html", "posts/index.html", "work/index.html"] + ARTICLES.map { |s| "posts/#{s}/index.html" }).each do |page|
  html = read(page) or next
  fail!("#{page}: renders the pixel crew, which belongs to the landing page only") if html.include?(crew_tag) || html.include?(crew_js)
end
fail!("assets/js/pixel-crew.js: not in the build") unless File.exist?(File.join(SITE, "assets/js/pixel-crew.js"))

# The crew is drawn on top of the text (issue #47). Behind it, every line of text
# scrolled through the sprites; on top, it only works because clicks, links and
# selection pass through it.
crew_rule = read("assets/css/site.css").to_s[/^#pixel-bg\s*\{[^}]*\}/m]
if crew_rule.nil?
  fail!("site.css: no #pixel-bg rule")
else
  z = crew_rule[/z-index:\s*(-?\d+)/, 1]
  fail!("site.css: #pixel-bg is not above the page text (z-index #{z.inspect})") unless z && z.to_i > 0
  fail!("site.css: #pixel-bg does not let clicks through (pointer-events: none)") unless crew_rule.include?("pointer-events: none")
end
# The home page fits on one screen since the redesign (#74), which put the footer right
# under the server. The footer leaves room for the crew on the page that has it.
unless read("assets/css/site.css").to_s.match?(/\.has-crew \.site-foot\s*\{\s*padding-bottom:\s*\d{3}px/)
  fail!("site.css: the footer does not leave room for the pixel crew (.has-crew .site-foot)")
end
fail!("index.html: the body does not mark the pixel crew (has-crew)") unless read("index.html").to_s.include?(%(<body class="has-crew">))

# The tag index must exist and list every tag actually in use.
tag_index = read("tags/index.html")
if tag_index.nil?
  fail!("tags/index.html: the tag index is missing")
else
  VOCABULARY.each do |tag|
    next unless File.file?(File.join(SITE, "tags", tag, "index.html"))
    fail!("tags/index.html: does not link /tags/#{tag}/") unless tag_index.include?(%(href="/tags/#{tag}/"))
  end
end

blog_index = read("posts/index.html")
if blog_index
  blog = blog_index.scan(%r{<script type="application/ld\+json">(.*?)</script>}m).flatten
                   .map { |b| JSON.parse(b) rescue nil }.compact
                   .find { |d| d["@type"] == "Blog" }
  if blog.nil?
    fail!("posts/index.html: no Blog structured data")
  elsif Array(blog["blogPost"]).length != ARTICLES.length
    fail!("posts/index.html: Blog lists #{Array(blog['blogPost']).length} posts, expected #{ARTICLES.length}")
  end
end

# 11. Liquid inside a code fence, in the SOURCE. This one cannot be caught in the build
#     output: Liquid runs before markdown, so `{{ ssh_port }}` in a fenced YAML block is
#     evaluated and silently replaced with nothing. The article renders, the build passes,
#     and the example is quietly wrong. The fix is {% raw %} around the fence; the check is
#     here because the symptom is an absence.
posts_dir = File.join(__dir__, "..", "_posts")
if File.directory?(posts_dir)
  Dir.glob(File.join(posts_dir, "*.md")).sort.each do |path|
    source = File.read(path)
    # Remove everything already protected, then look for a fence still holding Liquid.
    unprotected = source.gsub(/\{%\s*raw\s*%\}.*?\{%\s*endraw\s*%\}/m, "")
    unprotected.scan(/```[a-z]*\n(.*?)```/m).flatten.each do |block|
      next unless block.match?(/\{\{|\{%/)
      fail!("#{File.basename(path)}: a code block contains Liquid outside {% raw %} — " \
            "it will be evaluated away and the example will be silently wrong")
      break
    end
  end
end

# 11b. A fence meant to nest, in the SOURCE (#66). A code block that shows Markdown, such
#      as a README template inside a Python string, holds fences of its own. If the outer
#      fence is no longer than the inner ones, the inner closing fence ends the outer block
#      and everything after it pairs up wrong: code renders as prose, prose as code. The
#      build passes either way. A fence line inside a block that is as long as the block's
#      own fence and carries an info string is that mistake; lengthen the outer fence.
if File.directory?(posts_dir)
  Dir.glob(File.join(posts_dir, "*.md")).sort.each do |path|
    open_fence = nil
    File.foreach(path).with_index(1) do |line, number|
      fence = line[/\A {0,3}(`{3,}|~{3,})/, 1]
      if open_fence.nil?
        open_fence = [fence, number] if fence
      elsif fence && fence[0] == open_fence[0][0] && fence.length >= open_fence[0].length
        if line.strip.length > fence.length
          fail!("#{File.basename(path)}:#{number}: a fence inside the code block opened on line " \
                "#{open_fence[1]} is as long as its own, so it breaks the block instead of nesting; " \
                "lengthen the outer fence")
        else
          open_fence = nil
        end
      end
    end
  end
end

# 12. Article rendering. Code blocks are the most-read element on the site and were the
#     worst-looking one: an unscoped `code { border }` rule painted a box around every LINE,
#     because the <code> inside a <pre> is inline and spans many of them. These assertions
#     pin the shape of the fix rather than its appearance.
prose = read("assets/css/prose.css")
if prose.nil?
  fail!("assets/css/prose.css: missing — the article stylesheet")
else
  {
    ":not(pre) > code" => "inline code must be scoped away from <pre>, or every line gets its own box",
    "overflow-x: auto" => "a code block must scroll inside itself, never widen the page",
    ".table-scroll" => "a wide table must scroll inside itself",
    'content: attr(data-lang)' => "the language label",
    ".hljs-keyword" => "a highlight.js keyword colour, i.e. a syntax theme at all",
    ".hljs-string" => "a highlight.js string colour",
    ".hljs-comment" => "a highlight.js comment colour",
  }.each do |needle, why|
    fail!("prose.css: no #{needle.inspect} — #{why}") unless prose.include?(needle)
  end

  # The syntax colours are the design's tokens, defined once in site.css (#74).
  %w[--tk-kw --tk-str --tk-num --tk-com].each do |token|
    fail!("prose.css: the syntax theme does not use #{token}") unless prose.include?("var(#{token})")
  end
end

site_css = read("assets/css/site.css")
if site_css
  # Dark only (#74). One scheme means the two stylesheets cannot disagree about which
  # one is showing, which is how code blocks once went dark on a light page.
  fail!("site.css: not dark only (color-scheme: dark)") unless site_css.match?(/(?<![-\w])color-scheme:\s*dark\s*;/)
  fail!("site.css: still carries a light/dark switch") if site_css.include?("prefers-color-scheme") || site_css.include?("data-theme")
  if site_css.match?(/^code \{/)
    fail!("site.css: an unscoped `code {` rule is what boxed every line of every code block")
  end
end

# Every code block ships in the shape Medium's importer keeps (#56): a bare <pre>, <br> for
# line breaks, no highlighting <span>s, no <code> inside, no whitespace the importer would
# collapse (a line starting with a space or tab, two in a row, an empty line, &nbsp;
# anywhere but alone on an empty line), no trailing <br>, which makes the importer drop the
# block, no literal U+2007, U+2060 or U+00A0 that the site script could not tell from its
# own encoding, and nothing between </pre> and the next tag. A block
# with a language carries it as class and data-lang, and a page with code loads
# highlight.js, which restores the text and colours it. Any table must be wrapped. Both are
# done by _plugins/prose_markup.rb after conversion.
EXTRA_GRAMMARS = Array(YAML.safe_load(File.read("_config.yml"))["highlight_extra_languages"])
ARTICLES.each do |slug|
  html = read("posts/#{slug}/index.html") or next
  html.scan(%r{<pre\b([^>]*)>(.*?)</pre>}m).each do |attrs, block|
    if block.include?("<span")
      fail!("posts/#{slug}/: a code block carries highlighting markup, which Medium's importer drops")
    end
    if block.include?("<code")
      fail!("posts/#{slug}/: a code block has a <code> inside its <pre>, whose whitespace Medium's importer collapses")
    end
    if block.include?("\n")
      fail!("posts/#{slug}/: a code block has a raw newline, which Medium's importer collapses into one line; use <br>")
    end
    if block.end_with?("<br>")
      fail!("posts/#{slug}/: a code block ends in <br>, which makes Medium's importer drop it")
    end
    if block.match?(/[\u2007\u2060\u00a0]/)
      fail!("posts/#{slug}/: a code block has a literal U+2007, U+2060 or U+00A0, which the site script would rewrite")
    end
    block.split("<br>", -1).each do |line|
      next unless line.empty? || line.match?(/\A[ \t]|[ \t][ \t]/) || (line.include?("&nbsp;") && line != "&nbsp;")

      fail!("posts/#{slug}/: a code line #{line[0, 40].inspect} has whitespace Medium's importer collapses")
      break
    end
    unless attrs.empty? || attrs.match?(/\A class="language-([a-z0-9+#-]+)" data-lang="\1"\z/)
      fail!("posts/#{slug}/: <pre#{attrs}> is neither a bare <pre> nor one with a matching class and data-lang")
    end
  end
  if html.match?(%r{</pre>\s})
    fail!("posts/#{slug}/: whitespace after </pre>, which Medium's importer turns into an empty code block")
  end
  if html.include?("<pre") && !html.include?("/assets/vendor/highlight.js/highlight.min.js")
    fail!("posts/#{slug}/: has code but does not load highlight.js")
  end
  (["highlight.min.js"] + EXTRA_GRAMMARS.map { |g| "languages/#{g}.min.js" }).each do |file|
    next if File.file?(File.join(SITE, "assets/vendor/highlight.js", file))
    fail!("assets/vendor/highlight.js/#{file}: missing from the build")
  end
  tables = html.scan("<table>").length
  wrapped = html.scan('class="table-scroll"').length
  fail!("posts/#{slug}/: #{tables} table(s) but #{wrapped} scroll wrapper(s)") if tables != wrapped
end

# The style reference is what makes any of this reviewable.
style = read("style/index.html")
if style.nil?
  fail!("style/index.html: the style reference is missing")
else
  %w[blockquote <table> <kbd> <dl> <hr <ol <ul footnotes data-lang=].each do |component|
    fail!("style/index.html: does not render #{component}") unless style.include?(component)
  end
  langs = style.scan(/data-lang="([a-z]+)"/).flatten.uniq
  fail!("style/index.html: only #{langs.length} languages shown; the theme needs several to be reviewable") if langs.length < 4
end

# 13. The canonical domain.
cname = read("CNAME")&.strip
fail!("CNAME: is #{cname.inspect}, expected #{DOMAIN.inspect}") unless cname == DOMAIN

(%w[index.html about/index.html posts/index.html work/index.html] +
 ARTICLES.map { |a| "posts/#{a}/index.html" }).each do |page|
  html = read(page) or next
  fail!("#{page}: no canonical link to https://#{DOMAIN}") unless html.include?(%(rel="canonical" href="https://#{DOMAIN}))
end

# 14. llms-full.txt must actually carry the pages, not just its own header.
full = read("llms-full.txt")
if full
  ["Philipp Lehmann", "About", "Works", "OpenTaberna", "Impressum"].each do |title|
    fail!("llms-full.txt: page #{title.inspect} is missing") unless full.include?("# #{title}\n")
  end
  fail!("llms-full.txt: suspiciously short (#{full.length} bytes) — bodies did not render") if full.length < 40_000
  # A page copied in before it was rendered carries its template source instead of its
  # text (#74). No article uses Jekyll's include or assign, so either one here is a leak.
  full.scan(/\{%-?\s*(?:include|assign)\b[^%]*%\}/).uniq.first(3).each do |tag|
    fail!("llms-full.txt: carries unrendered Liquid #{tag.inspect}")
  end
  fail!("llms-full.txt: the About git log is missing") unless full.include?("git init ~/philipp")
end

# 15. No file that gets served should leak an unrendered Liquid tag.
# llms-full.txt is deliberately NOT in this list. It carries the full text of every
# article, and an article about Ansible templating or Argo CD's Go templates contains
# `{% ... %}` as its SUBJECT. Scanning it for braces cannot tell a rendering failure from
# a code example. Articles are covered instead by the source-level check above, which
# looks at the one place the distinction is unambiguous.
%w[llms.txt ai.txt humans.txt robots.txt .well-known/security.txt profile.json resume.json].each do |f|
  body = read(f) or next
  fail!("#{f}: contains unrendered Liquid") if body.include?("{{") || body.include?("{%")
end

# 16. The home page says who he is in visible text (#74). The design's home is a short
#     whoami; a name search needs the name as the page heading, the role beside it, and
#     the employer, the university and OpenTaberna in the prose a summary is built from.
if (home = read("index.html"))
  fail!("index.html: no <h1> with the name") unless home.match?(%r{<h1[^>]*>\s*Philipp Lehmann\s*</h1>})
  fail!("index.html: no role line") unless home.include?("CTO at Nerd Force1 UG · infrastructure engineer · Bochum, Germany")
  whoami = home[%r{<section class="whoami".*?</section>}m].to_s
  fail!("index.html: no whoami section") if whoami.empty?
  ["Nerd Force1 UG", "AI-Gruppe", "Ruhr University Bochum", "OpenTaberna"].each do |name|
    fail!("index.html: the whoami does not name #{name}") unless whoami.include?(name)
  end
  PROFILES.each do |me|
    fail!("index.html: the profile links do not include #{me}") unless home.include?(%(href="#{me}"))
  end
end

# 17. Every page, not a sample: the footer links the Impressum, and nothing is loaded
#     from another origin (#74). Fonts, scripts and styles are served from the site, so
#     no reader's address goes to a third party, which the colophon has always promised.
html_pages = Dir.glob(File.join(SITE, "**", "*.html")).select { |f| File.read(f, 200).include?("<!DOCTYPE html>") }
fail!("_site: suspiciously few HTML pages (#{html_pages.length})") if html_pages.length < 100
html_pages.each do |path|
  rel = path.delete_prefix("#{SITE}/")
  html = File.read(path)
  fail!("#{rel}: does not link the Impressum") unless html.include?(%(href="/impressum/"))
  html.scan(/<(?:script|img|iframe|source|video|audio)\b[^>]*\ssrc="((?:https?:)?\/\/[^"]+)"/).flatten.each do |url|
    fail!("#{rel}: loads #{url} from another origin")
  end
  html.scan(/<link\b[^>]*>/).each do |tag|
    next unless tag.match?(/rel="(?:stylesheet|preload|modulepreload|icon|prefetch|preconnect|dns-prefetch)"/)
    url = tag[/href="([^"]+)"/, 1].to_s
    fail!("#{rel}: loads #{url} from another origin") if url.match?(%r{\A(?:https?:)?//})
  end
end
Dir.glob(File.join(SITE, "assets", "css", "*.css")).each do |path|
  File.read(path).scan(/(?:url\(|@import\s+)\s*['"]?((?:https?:)?\/\/[^'")\s]+)/).flatten.each do |url|
    fail!("#{File.basename(path)}: loads #{url} from another origin")
  end
end
%w[Inter-latin.woff2 mononoki-Regular.woff2 mononoki-Bold.woff2 mononoki-Italic.woff2
   Inter-OFL.txt mononoki-OFL.txt].each do |font|
  fail!("assets/fonts/#{font}: missing from the build") unless File.file?(File.join(SITE, "assets", "fonts", font))
end

# 18. Work (#74): every project has its own page with structured data naming him as its
#     creator, a place in the /work/ ItemList, and an entry in resume.json, so the page a
#     reader sees and the CV a machine reads list the same things. A repository is linked
#     only if it is public.
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
    fail!("work/index.html: the ItemList does not name #{p['title']}") unless named
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
  if node.nil?
    fail!("work/#{p['id']}/: no project structured data")
  else
    fail!("work/#{p['id']}/: creator must reference #{PERSON_ID}") unless node.dig("creator", "@id") == PERSON_ID
    fail!("work/#{p['id']}/: structured data names #{node['name'].inspect}") unless node["name"] == p["title"]
  end
  in_resume = Array(resume&.dig("projects")).any? { |r| r["name"] == p["title"] } ||
              Array(resume&.dig("work")).any? { |w| w["name"] == p["title"] }
  fail!("resume.json: does not carry the Work project #{p['title']}") unless in_resume
  repo = p["repo"].to_s[%r{github\.com/PhilippTheServer/([^/]+)}, 1]
  fail!("_work/#{p['id']}.md: links #{repo}, which is not a public repository") if repo && !PUBLIC_REPOS.include?(repo)
  html.scan(%r{github\.com/PhilippTheServer/([A-Za-z0-9._-]+)}).flatten.uniq.each do |r|
    fail!("work/#{p['id']}/: links github.com/PhilippTheServer/#{r}, which is not a public repository") unless PUBLIC_REPOS.include?(r)
  end
end
Array(resume&.dig("projects")).each do |r|
  fail!("resume.json: project #{r['name']} has no Work page") unless WORK.any? { |p| p["title"] == r["name"] }
end
if (full_text = read("llms-full.txt"))
  WORK.each do |p|
    first_words = p["description"].to_s.split.first(8).join(" ")
    fail!("llms-full.txt: does not carry the description of #{p['title']}") unless full_text.include?(first_words)
  end
end

# 19. The git log on /about/ tells the same career as resume.json (#74). Each Nerd
#     Force1 role is a commit dated with the role's start, and every commit is a
#     <details>, so its story is in the HTML rather than behind a script.
career_path = File.join(__dir__, "..", "_data", "career.yml")
career_log = File.file?(career_path) ? YAML.safe_load(File.read(career_path)) : (fail!("_data/career.yml: missing"); [])
CAREER.each do |role|
  entry = career_log.find { |c| c["role"] == role["position"] }
  if entry.nil?
    fail!("_data/career.yml: no commit for #{role['position']}")
  elsif entry["start"] != role["startDate"]
    fail!("_data/career.yml: #{role['position']} starts #{entry['start'].inspect}, resume.json says #{role['startDate'].inspect}")
  end
end
if (about = read("about/index.html"))
  # The stack list was cut from the page on purpose (#78); resume.json and llms.txt keep it.
  fail!("about/index.html: the stack section is back") if about.match?(%r{<h2[^>]*>\s*The stack}i)
  commits = about.scan(/<details class="commit[ "]/).length
  fail!("about/index.html: #{commits} commits rendered, _data/career.yml has #{career_log.length}") if commits != career_log.length
  career_log.each do |c|
    fail!("about/index.html: commit #{c['hash']} is missing") unless about.include?(">#{c['hash']}<")
    fail!("about/index.html: the story of #{c['hash']} is missing") unless about.include?(CGI.escapeHTML(c["story"]))
  end
end

if @failures.empty?
  puts "check_site.rb: all checks passed"
  exit 0
else
  warn "check_site.rb: #{@failures.length} failure(s)"
  @failures.each { |f| warn "  ✗ #{f}" }
  exit 1
end

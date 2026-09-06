#!/usr/bin/env ruby
# Structural checks on the built site. Run by scripts/verify.sh and by CI.
# Exits non-zero, listing every failure, if the site stops holding together.

require "json"

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
  index.html about/index.html posts/index.html
  llms.txt llms-full.txt profile.json resume.json
  ai.txt humans.txt robots.txt .well-known/security.txt
  feed.xml sitemap.xml favicon.svg assets/css/site.css CNAME
] + ARTICLES.map { |a| "posts/#{a}/index.html" }).each do |f|
  fail!("#{f}: missing from the built site") unless File.file?(File.join(SITE, f))
end

# The repository listing was replaced by the articles; it must not come back.
fail!("projects/index.html: the repo list should be gone") if File.file?(File.join(SITE, "projects/index.html"))

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

# 2. The two JSON files must parse.
profile = parse_json("profile.json")
resume  = parse_json("resume.json")

# 3. profile.json must stay a usable schema.org Person.
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

# 4. resume.json must stay valid JSON Resume.
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

# 5. The JSON-LD embedded in each page must be byte-for-byte the same object as
#    profile.json — the page and the file can never claim different things.
(%w[index.html about/index.html posts/index.html] +
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

# 6. The ORCID iD is what binds every one of these files to one person.
%w[
  profile.json resume.json llms.txt llms-full.txt
  ai.txt humans.txt index.html about/index.html
].each do |f|
  body = read(f) or next
  fail!("#{f}: the ORCID iD #{ORCID} is missing") unless body.include?(ORCID)
end

# 7. Retired projects must not reappear. neteye was dropped from the site deliberately
#    (issue #3); it lives on in the GitHub account but is not presented as current work.
#    Every generated file derives from the pages, so one stray card would put it back in
#    resume.json, llms-full.txt and the JSON-LD at once.
RETIRED = %w[neteye].freeze

RETIRED.each do |name|
  %w[
    index.html about/index.html projects/index.html
    llms.txt llms-full.txt profile.json resume.json
  ].each do |f|
    body = read(f) or next
    fail!("#{f}: mentions retired project #{name.inspect}") if body.downcase.include?(name)
  end
end

# 8. Two facts I got wrong once, from sources that looked authoritative (issue #7).
#
#    He became CTO on 2026-09-06. Before that he was Head of Administration and IT at the
#    same employer since 2022-03-01, which is what the ORCID record still lists — his
#    previous title, not an error. Two work entries, because one entry carrying the new
#    title and the old start date would claim he has been CTO since 2022.
CTO_SINCE = "2026-09-06"

if profile
  fail!("profile.json: jobTitle is #{profile['jobTitle'].inspect}, expected \"CTO\"") unless profile["jobTitle"] == "CTO"
end

if resume
  work = Array(resume["work"])
  current = work.first

  fail!("resume.json: work[0].position is #{current&.dig('position').inspect}, expected \"CTO\"") unless current&.dig("position") == "CTO"
  unless current&.dig("startDate") == CTO_SINCE
    fail!("resume.json: the CTO entry starts #{current&.dig('startDate').inspect}, expected #{CTO_SINCE.inspect} — the promotion date, not the date he joined")
  end
  fail!("resume.json: the current role must not carry an endDate") if current&.key?("endDate")

  previous = work[1]
  unless previous && previous["position"] == "Head of Administration and IT"
    fail!("resume.json: work[1] must be the previous role at the same employer")
  end
  if previous && previous["endDate"] != CTO_SINCE
    fail!("resume.json: the previous role must end when the CTO role starts (#{CTO_SINCE})")
  end
  if previous && previous["startDate"] != "2022-03-01"
    fail!("resume.json: the previous role must start 2022-03-01, when he joined")
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

llms = read("llms.txt")
llms&.each_line&.with_index(1) do |line, n|
  next unless line.include?("WireGuard")
  next if line.include?("NetBird")
  # An entry in the generated writing list is an article title, not a claim about the
  # estate. The rule is about the prose that describes what he administers.
  next if line.start_with?("- [") && line.include?("/posts/")
  fail!("llms.txt:#{n}: names WireGuard without NetBird beside it")
end

# 9. Article structured data. Descriptive titles and a sitemap get a page crawled;
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

# 10. Liquid inside a code fence, in the SOURCE. This one cannot be caught in the build
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

# 11. The canonical domain.
cname = read("CNAME")&.strip
fail!("CNAME: is #{cname.inspect}, expected #{DOMAIN.inspect}") unless cname == DOMAIN

(%w[index.html about/index.html posts/index.html] +
 ARTICLES.map { |a| "posts/#{a}/index.html" }).each do |page|
  html = read(page) or next
  fail!("#{page}: no canonical link to https://#{DOMAIN}") unless html.include?(%(rel="canonical" href="https://#{DOMAIN}))
end

# 12. llms-full.txt must actually carry the pages, not just its own header.
full = read("llms-full.txt")
if full
  ["Philipp Lehmann", "About"].each do |title|
    fail!("llms-full.txt: page #{title.inspect} is missing") unless full.include?("# #{title}\n")
  end
  fail!("llms-full.txt: suspiciously short (#{full.length} bytes) — bodies did not render") if full.length < 40_000
end

# 13. No file that gets served should leak an unrendered Liquid tag.
# llms-full.txt is deliberately NOT in this list. It carries the full text of every
# article, and an article about Ansible templating or Argo CD's Go templates contains
# `{% ... %}` as its SUBJECT. Scanning it for braces cannot tell a rendering failure from
# a code example. Articles are covered instead by the source-level check above, which
# looks at the one place the distinction is unambiguous.
%w[llms.txt ai.txt humans.txt robots.txt .well-known/security.txt profile.json resume.json].each do |f|
  body = read(f) or next
  fail!("#{f}: contains unrendered Liquid") if body.include?("{{") || body.include?("{%")
end

if @failures.empty?
  puts "check_site.rb: all checks passed"
  exit 0
else
  warn "check_site.rb: #{@failures.length} failure(s)"
  @failures.each { |f| warn "  ✗ #{f}" }
  exit 1
end

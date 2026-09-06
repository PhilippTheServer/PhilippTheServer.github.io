#!/usr/bin/env ruby
# Structural checks on the built site. Run by scripts/verify.sh and by CI.
# Exits non-zero, listing every failure, if the site stops holding together.

require "json"

SITE   = ARGV[0] || "_site"
ORCID  = "0009-0002-3922-2471"
DOMAIN = "philipptheserver.com"

# Every article the site promises. Listed here rather than globbed, so deleting one is a
# deliberate edit to this file instead of a silent disappearance.
ARTICLES = %w[
  infrastructure-as-code
  kubernetes
  ceph
  observatory-monitoring
  netbird-vpn
  keycloak
  atlas-agentic-ops
].freeze

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

  feed = read("feed.xml")
  fail!("feed.xml: does not carry #{url}") if feed && !feed.include?("https://#{DOMAIN}#{url}")

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

# 8. The canonical domain.
cname = read("CNAME")&.strip
fail!("CNAME: is #{cname.inspect}, expected #{DOMAIN.inspect}") unless cname == DOMAIN

(%w[index.html about/index.html posts/index.html] +
 ARTICLES.map { |a| "posts/#{a}/index.html" }).each do |page|
  html = read(page) or next
  fail!("#{page}: no canonical link to https://#{DOMAIN}") unless html.include?(%(rel="canonical" href="https://#{DOMAIN}))
end

# 9. llms-full.txt must actually carry the pages, not just its own header.
full = read("llms-full.txt")
if full
  ["Philipp Lehmann", "About"].each do |title|
    fail!("llms-full.txt: page #{title.inspect} is missing") unless full.include?("# #{title}\n")
  end
  fail!("llms-full.txt: suspiciously short (#{full.length} bytes) — bodies did not render") if full.length < 40_000
  fail!("llms-full.txt: contains unrendered Liquid") if full.include?("{{") || full.include?("{%")
end

# 10. No file that gets served should leak an unrendered Liquid tag.
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

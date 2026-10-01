# frozen_string_literal: true

# Generates one page per tag, plus an index of all of them.
#
# A label that cannot be clicked is a decoration. These pages are what make the controlled
# vocabulary in _data/tags.yml worth having: /tags/ceph/ is a real page a reader can reach,
# a search engine can index, and the sitemap can list.
#
# The vocabulary is CLOSED. A tag on an article that is not in _data/tags.yml aborts the
# build rather than quietly generating a page nobody planned — that is the whole point of
# a controlled vocabulary, and it is worth nothing if it is only advisory.

module Jekyll
  class TagPage < Page
    def initialize(site, base, tag, posts, description)
      @site = site
      @base = base
      @dir  = File.join("tags", tag)
      @name = "index.html"

      process(@name)
      self.data = {
        "layout" => "default",
        "title" => tag,
        "subtitle" => description,
        "tag" => tag,
        "posts" => posts.sort_by { |p| p.data["date"] }.reverse,
        "description" =>
          "#{posts.length} #{posts.length == 1 ? 'article' : 'articles'} by Philipp Lehmann " \
          "on #{tag}: #{description}",
      }
      self.content = <<~LIQUID
        {% include post-list.html posts=page.posts %}
        <p class="list-foot"><a href="/tags/">All topics</a> · <a href="/posts/">All writing</a></p>
      LIQUID
    end
  end

  class TagIndex < Page
    def initialize(site, base, counts, descriptions)
      @site = site
      @base = base
      @dir  = "tags"
      @name = "index.html"

      process(@name)
      rows = counts.sort_by { |tag, n| [-n, tag] }.map do |tag, n|
        <<~ROW
          <li>
            <a class="tag-label" href="/tags/#{tag}/" style="--h: #{TagHue.of(tag)}">#{tag}</a>
            <span class="tag-count">#{n}</span>
            <p>#{descriptions[tag]}</p>
          </li>
        ROW
      end

      self.data = {
        "layout" => "default",
        "title" => "Topics",
        "subtitle" => "Every article, grouped by what it is about.",
        "description" =>
          "All #{counts.length} topics covered on philipptheserver.com, from Ansible and " \
          "Ceph to Kubernetes, observability and self-hosted language models.",
      }
      self.content = %(<ul class="tag-index">\n#{rows.join}\n</ul>\n)
    end
  end

  class TagPageGenerator < Generator
    safe true
    priority :normal

    def generate(site)
      vocabulary = site.data["tags"]
      raise "_data/tags.yml is missing — the tag vocabulary is required" if vocabulary.nil?

      used = Hash.new { |h, k| h[k] = [] }
      site.posts.docs.each do |post|
        Array(post.data["tags"]).each { |tag| used[tag] << post }
      end

      unknown = used.keys - vocabulary.keys
      unless unknown.empty?
        raise "unknown tags #{unknown.sort.inspect} — add them to _data/tags.yml or fix the " \
              "articles. The vocabulary is closed on purpose: an ad-hoc tag filters nothing."
      end

      counts = {}
      used.each do |tag, posts|
        site.pages << TagPage.new(site, site.source, tag, posts, vocabulary[tag])
        counts[tag] = posts.length
      end
      site.pages << TagIndex.new(site, site.source, counts, vocabulary)
    end
  end
end

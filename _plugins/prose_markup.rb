# frozen_string_literal: true

# Three things an article's HTML needs that kramdown does not produce.
#
# 1. The language label on a code block. kramdown writes `<pre><code class="language-yaml">`,
#    which CSS can only turn into a label with one rule per language — a list that goes
#    stale the first time an article uses a language nobody added. Copying it into
#    `data-lang` on the <pre> lets one CSS rule cover every language there will ever be.
#    The markup otherwise stays plain, because Medium's importer drops Rouge's
#    span-per-token output (#56); highlight.js colours it in the browser.
#
# 2. A scroll container around tables. A wide table has to scroll inside itself; without a
#    wrapper the only element that can scroll is the page, and a page that scrolls
#    sideways on a phone is the bug this prevents.
#
# 3. Line breaks in code as <br>. Medium's importer treats the newlines inside a <pre> as
#    plain whitespace and turns a whole block into one line (#56); its own editor
#    separates code lines with <br>. assets/js/highlight.js turns them back into newlines
#    before highlighting, so readers see the same block either way.
#
# Runs on rendered post HTML only, after conversion, so the source stays plain Markdown.

module Jekyll
  module ProseMarkup
    LANGUAGE = /<pre><code class="language-([a-z0-9+#-]+)">/i.freeze
    TABLE = %r{<table>(.*?)</table>}m.freeze
    CODE = %r{(<pre\b[^>]*><code\b[^>]*>)(.*?)(</code></pre>)}m.freeze

    def self.apply(html)
      html = html.gsub(LANGUAGE) do
        %(<pre data-lang="#{Regexp.last_match(1)}"><code class="language-#{Regexp.last_match(1)}">)
      end

      html = html.gsub(CODE) do
        open, body, close = Regexp.last_match.captures
        "#{open}#{body.chomp.gsub("\n", "<br>")}#{close}"
      end

      html.gsub(TABLE) do
        %(<div class="table-scroll"><table>#{Regexp.last_match(1)}</table></div>)
      end
    end
  end
end

Jekyll::Hooks.register :documents, :post_render do |doc|
  next unless doc.output_ext == ".html"
  next unless doc.collection&.label == "posts"

  doc.output = Jekyll::ProseMarkup.apply(doc.output)
end

# A page can use the article layout too — /style/ does, and it is the one page whose whole
# purpose is showing these components. Keyed on the layout rather than the path, so the
# rule is "anything that renders as an article gets article markup".
Jekyll::Hooks.register :pages, :post_render do |page|
  next unless page.output_ext == ".html"
  next unless page.data["layout"] == "post"

  page.output = Jekyll::ProseMarkup.apply(page.output)
end

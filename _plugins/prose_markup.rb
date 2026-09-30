# frozen_string_literal: true

# Two things an article's HTML needs that kramdown does not produce.
#
# 1. Code blocks in the shape of Medium's own: a bare <pre>, <br> for each line break,
#    indentation as plain spaces, no <code> inside. Medium's importer keeps nothing else
#    (#56): it drops Rouge's span-per-token markup, and it collapses the whitespace of a
#    <code> inside a <pre>, newlines and indentation alike. kramdown writes
#    `<pre><code class="language-yaml">`; the language moves onto the <pre>, as the class
#    highlight.js reads and as `data-lang`, which one CSS rule turns into the label for
#    every language there will ever be. assets/js/highlight.js turns the <br>s back into
#    newlines and colours the block in the browser.
#
# 2. A scroll container around tables. A wide table has to scroll inside itself; without a
#    wrapper the only element that can scroll is the page, and a page that scrolls
#    sideways on a phone is the bug this prevents.
#
# Runs on rendered post HTML only, after conversion, so the source stays plain Markdown.

module Jekyll
  module ProseMarkup
    CODE = %r{<pre><code(?: class="language-([a-z0-9+#-]+)")?>(.*?)</code></pre>}m.freeze
    TABLE = %r{<table>(.*?)</table>}m.freeze

    def self.apply(html)
      html = html.gsub(CODE) do
        lang, body = Regexp.last_match.captures
        attrs = lang ? %( class="language-#{lang}" data-lang="#{lang}") : ""
        "<pre#{attrs}>#{body.chomp.gsub("\n", "<br>")}</pre>"
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

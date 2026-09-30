# frozen_string_literal: true

# Two things an article's HTML needs that kramdown does not produce.
#
# 1. Code blocks in a shape Medium's importer keeps (#56): a bare <pre> with no <code>
#    inside, <br> for each line break, and nothing after it but the next tag. The importer
#    drops Rouge's span-per-token markup, collapses runs of spaces and &nbsp; alike, drops
#    tabs, splits a block at an empty line, and drops a block that ends in <br>. Figure
#    spaces (U+2007) survive, so a space at the start of a line or next to other
#    whitespace becomes a figure space, a tab becomes a word joiner (U+2060) plus four
#    figure spaces, and an empty line holds a single &nbsp;. kramdown
#    writes `<pre><code class="language-yaml">`; the language moves onto the <pre>, as the
#    class highlight.js reads and as `data-lang`, which one CSS rule turns into the label
#    for every language there will ever be. assets/js/highlight.js restores the exact
#    text and colours the block in the browser.
#
# 2. A scroll container around tables. A wide table has to scroll inside itself; without a
#    wrapper the only element that can scroll is the page, and a page that scrolls
#    sideways on a phone is the bug this prevents.
#
# Runs on rendered post HTML only, after conversion, so the source stays plain Markdown.

module Jekyll
  module ProseMarkup
    CODE = %r{<pre><code(?: class="language-([a-z0-9+#-]+)")?>(.*?)</code></pre>\s*}m.freeze
    TABLE = %r{<table>(.*?)</table>}m.freeze
    NBSP = "&nbsp;"
    FIGURE = "&#8199;"
    TAB = "&#8288;#{FIGURE * 4}".freeze

    def self.apply(html)
      html = html.gsub(CODE) do
        lang, body = Regexp.last_match.captures
        attrs = lang ? %( class="language-#{lang}" data-lang="#{lang}") : ""
        "<pre#{attrs}>#{body.chomp.split("\n", -1).map { |line| importable(line) }.join("<br>")}</pre>"
      end

      html.gsub(TABLE) do
        %(<div class="table-scroll"><table>#{Regexp.last_match(1)}</table></div>)
      end
    end

    def self.importable(line)
      return NBSP if line.empty?

      line.each_char.with_index.map do |char, i|
        case char
        when "\t" then TAB
        when " "
          lone = i.positive? && !" \t".include?(line[i - 1]) && line[i + 1] != " "
          lone ? " " : FIGURE
        else char
        end
      end.join
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

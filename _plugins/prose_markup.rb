# frozen_string_literal: true

# Two things an article's HTML needs that kramdown and Rouge do not produce.
#
# 1. The language label on a code block. Rouge writes `class="language-yaml
#    highlighter-rouge"`, which CSS can only turn into a label with one rule per language —
#    a list that goes stale the first time an article uses a language nobody added. Copying
#    it into `data-lang` lets one CSS rule cover every language there will ever be.
#
# 2. A scroll container around tables. A wide table has to scroll inside itself; without a
#    wrapper the only element that can scroll is the page, and a page that scrolls
#    sideways on a phone is the bug this prevents.
#
# Runs on rendered post HTML only, after conversion, so the source stays plain Markdown.

module Jekyll
  module ProseMarkup
    LANGUAGE = /<div class="language-([a-z0-9+#-]+) highlighter-rouge"/i.freeze
    TABLE = %r{<table>(.*?)</table>}m.freeze

    def self.apply(html)
      html = html.gsub(LANGUAGE) do
        %(<div data-lang="#{Regexp.last_match(1)}" class="language-#{Regexp.last_match(1)} highlighter-rouge")
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

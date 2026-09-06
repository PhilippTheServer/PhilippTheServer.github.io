# frozen_string_literal: true

# Converts rendered page HTML into readable plain text for /llms-full.txt.
#
# Liquid's `strip_html` throws away every block boundary, which turns a page
# into one unbroken paragraph: headings run into body text, adjacent tags
# concatenate, and link targets vanish. This filter keeps the structure a
# reader — human or model — needs: headings stay headings, list items stay
# separate lines, and links keep their URL.

require "nokogiri"

module Jekyll
  module HtmlToTextFilter
    HEADINGS = { "h1" => "#", "h2" => "##", "h3" => "###", "h4" => "####" }.freeze
    BLOCKS = %w[p div section article header footer blockquote pre table tr].freeze

    def html_to_text(input)
      fragment = Nokogiri::HTML5.fragment(input.to_s)
      out = +""
      walk(fragment, out)
      tidy(out)
    end

    private

    def walk(node, out)
      node.children.each do |child|
        case child.type
        when Nokogiri::XML::Node::TEXT_NODE
          out << child.text.gsub(/\s+/, " ")
        when Nokogiri::XML::Node::ELEMENT_NODE
          element(child, out)
        end
      end
    end

    def element(node, out)
      name = node.name.downcase

      case name
      when "script", "style", "svg", "head"
        nil
      when *HEADINGS.keys
        out << "\n\n#{HEADINGS[name]} "
        walk(node, out)
        out << "\n\n"
      when "li"
        # A card-style item wraps its own heading; bulleting it as well would
        # leave a dangling "-" on a line of its own.
        if node.at_xpath("./h1|./h2|./h3|./h4")
          out << "\n\n"
          walk(node, out)
          out << "\n\n"
        else
          out << "\n- "
          definition_item(node, out) || walk(node, out)
        end
      when "ul", "ol"
        out << "\n"
        walk(node, out)
        out << "\n\n"
      when "a"
        walk(node, out)
        href = node["href"].to_s
        out << " (#{href})" if href.start_with?("http", "mailto:")
      when "br"
        out << "\n"
      when "span", "code", "em", "strong", "b", "i"
        walk(node, out)
        out << " "
      when *BLOCKS
        out << "\n\n"
        walk(node, out)
        out << "\n\n"
      else
        walk(node, out)
      end
    end

    # `<li><span class="k">Key</span> <span>value</span></li>` reads far better
    # as "- Key: value" than as "- Key value".
    def definition_item(node, out)
      key = node.at_xpath("./span[@class='k']")
      return false if key.nil?

      out << "#{key.text.strip}: "
      key.remove
      walk(node, out)
      true
    end

    def tidy(text)
      text.gsub(/[ \t]+/, " ")
          .gsub(/ +\n/, "\n")
          .gsub(/\n +/, "\n")
          .gsub(/\n{3,}/, "\n\n")
          .strip
    end
  end
end

Liquid::Template.register_filter(Jekyll::HtmlToTextFilter)

# frozen_string_literal: true

# The hue of a tag label, from the design handoff (#74): a hash of the tag name, so every
# tag keeps its colour on every page and a new tag needs no palette entry. Computed at
# build time, so the labels are coloured without any script.
module Jekyll
  module TagHue
    def self.of(tag) = tag.to_s.each_char.reduce(0) { |h, c| (h * 31 + c.ord) % 360 }

    def tag_hue(tag) = TagHue.of(tag)
  end
end

Liquid::Template.register_filter(Jekyll::TagHue)

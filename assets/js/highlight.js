// Colours code blocks in the browser. The HTML carries each block in the shape Medium's
// importer keeps (#56): a bare <pre>, <br> for line breaks, a figure space (U+2007) where a
// space would be collapsed, a word joiner (U+2060) plus four figure spaces for a tab, and a
// single &nbsp; on an empty line. This restores the exact text first; no code source
// contains those characters, so the restore is lossless.
hljs.registerAliases(["jinja"], { languageName: "django" });
hljs.registerAliases(["text", "hcl"], { languageName: "plaintext" });
document.querySelectorAll(".prose pre").forEach((pre) => {
  pre.querySelectorAll("br").forEach((br) => br.replaceWith("\n"));
  pre.textContent = pre.textContent
    .split("\n")
    .map((line) =>
      line === "\u00a0"
        ? ""
        : line.replaceAll("\u2060\u2007\u2007\u2007\u2007", "\t").replaceAll("\u2007", " "),
    )
    .join("\n");
  if (pre.dataset.lang) hljs.highlightElement(pre);
});

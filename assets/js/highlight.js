// Colours code blocks in the browser. The HTML carries each block in the shape Medium's
// importer keeps (#56): a bare <pre>, <br> for line breaks, and &nbsp; where a space or
// tab would be collapsed. This restores the exact text first; no code source contains a
// real &nbsp;, so the restore is lossless.
hljs.registerAliases(["jinja"], { languageName: "django" });
hljs.registerAliases(["text", "hcl"], { languageName: "plaintext" });
document.querySelectorAll(".prose pre").forEach((pre) => {
  pre.querySelectorAll("br").forEach((br) => br.replaceWith("\n"));
  pre.textContent = pre.textContent
    .split("\n")
    .map((line) =>
      line === "\u00a0" ? "" : line.replaceAll("\u00a0\t", "\t").replaceAll("\u00a0", " "),
    )
    .join("\n");
  if (pre.dataset.lang) hljs.highlightElement(pre);
});

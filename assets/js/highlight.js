// Colours code blocks in the browser. The HTML is a bare <pre> with <br> line breaks, the
// shape of Medium's own code blocks, because that is what its importer keeps (#56).
// highlight.js reads textContent, so the <br>s become newlines first.
hljs.registerAliases(["jinja"], { languageName: "django" });
hljs.registerAliases(["text", "hcl"], { languageName: "plaintext" });
document.querySelectorAll("pre[data-lang]").forEach((pre) => {
  pre.querySelectorAll("br").forEach((br) => br.replaceWith("\n"));
  hljs.highlightElement(pre);
});

// Colours code blocks in the browser. The HTML keeps plain <pre><code>, with <br> for line
// breaks, because that is the form Medium's importer keeps (#56). highlight.js reads
// textContent, so the <br>s become newlines first.
document.querySelectorAll("pre > code br").forEach((br) => br.replaceWith("\n"));
hljs.registerAliases(["jinja"], { languageName: "django" });
hljs.registerAliases(["text", "hcl"], { languageName: "plaintext" });
hljs.highlightAll();

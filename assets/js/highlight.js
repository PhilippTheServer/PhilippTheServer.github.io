// Colours code blocks in the browser. The HTML keeps plain <pre><code> because Medium's
// importer drops server-side highlighting markup (#56).
hljs.registerAliases(["jinja"], { languageName: "django" });
hljs.registerAliases(["text", "hcl"], { languageName: "plaintext" });
hljs.highlightAll();

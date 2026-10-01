// The few things the site does in the browser (#74): copy the feed URL, the Konami
// code, and a coffee for whoever opens the console. Nothing on the page depends on it.
(() => {
  const toast = document.querySelector(".toast");
  let timer;
  const flash = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => { toast.hidden = true; }, 3200);
  };

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        flash(button.dataset.toast || "copied.");
      } catch {
        flash("could not copy. the link next to the button works too.");
      }
    });
  });

  const konami = "ArrowUp,ArrowUp,ArrowDown,ArrowDown,ArrowLeft,ArrowRight,ArrowLeft,ArrowRight,b,a";
  let keys = [];
  addEventListener("keydown", (event) => {
    keys = [...keys, event.key].slice(-10);
    if (keys.join() === konami) {
      keys = [];
      flash("+30 lives. spend them on the backlog.");
    }
  });

  console.log(
    [
      "      ( (",
      "       ) )",
      "    ........",
      "    |      |]",
      "    \\      /",
      "     `----'",
      "",
      "philipp@theserver:~$ the coffee is warm.",
      "the source of this site: https://github.com/PhilippTheServer/PhilippTheServer.github.io",
    ].join("\n"),
  );
})();

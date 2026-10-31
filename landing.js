// Landing page extras. Kept in a file of its own because the server's content policy only allows scripts from this origin.
(() => {
  // A picture that fails to load (a copy without the sample pictures) just drops out of the collage.
  for (const img of document.querySelectorAll(".pin img")) {
    const drop = () => { img.closest("figure").hidden = true; };
    if (img.complete && img.naturalWidth === 0) drop(); else img.addEventListener("error", drop);
  }

  // Show the embedded playground only where there is one (the public demo), and only load it when scrolled near.
  (async () => {
    try {
      const caps = await (await fetch("/api/capabilities")).json();
      if (!caps.demo) return;
      const section = document.getElementById("try"), frame = document.getElementById("playground");
      section.hidden = false;
      const load = () => { if (!frame.src) frame.src = "/board"; };
      if ("IntersectionObserver" in window) new IntersectionObserver((entries, io) => { if (entries.some(e => e.isIntersecting)) { load(); io.disconnect(); } }, { rootMargin: "300px" }).observe(section);
      else load();

      // The board pans on scroll, so a page scrolling past it would get stuck inside it. Keep it behind a cover until
      // someone clicks, and put the cover back as soon as they move away.
      const stage = document.getElementById("playground-stage"), lock = document.getElementById("playground-lock"), live = document.getElementById("playground-live");
      const touch = matchMedia("(hover: none)").matches;
      const setLive = on => {
        stage.classList.toggle("live", on);
        live.hidden = !on;
        live.textContent = touch ? "Live. Tap outside to scroll the page." : "Live. Move away to scroll the page.";
        if (on) { load(); frame.focus({ preventScroll: true }); }
        else if (document.activeElement === frame) frame.blur();
      };
      lock.addEventListener("click", () => setLive(true));
      stage.addEventListener("mouseleave", () => { if (!touch) setLive(false); });
      document.addEventListener("pointerdown", e => { if (!stage.contains(e.target)) setLive(false); });
    } catch {}
  })();
})();

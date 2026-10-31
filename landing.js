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
    } catch {}
  })();
})();

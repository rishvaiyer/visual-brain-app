// Visual Brain, extras on top of app.js: a welcome card, a big-picture viewer, a dismissable playground notice.
// Loads after app.js and uses its globals (state, $, renderAll, openAddImages, ...).
(() => {
  if (window.self !== window.top) document.documentElement.classList.add("framed");  // embedded on the landing page
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch {} },
  };

  // Playground notice: close it for the rest of the visit. app.js re-asserts [hidden] on every refresh, so use a class.
  $("#demo-dismiss")?.addEventListener("click", () => {
    $("#demo-banner").classList.add("dismissed");
    try { sessionStorage.setItem("vb-banner-closed", "1"); } catch {}
  });
  try { if (sessionStorage.getItem("vb-banner-closed")) $("#demo-banner")?.classList.add("dismissed"); } catch {}
  const bannerCss = document.createElement("style");
  bannerCss.textContent = "#demo-banner.dismissed{display:none!important}";
  document.head.append(bannerCss);

  function searchFor(term) {
    state.boardId = "main"; state.selected.clear(); renderAll();
    const input = $("#visual-query"); input.value = term; updateVisualQuery(); input.focus({ preventScroll: true });
  }
  function openBoard(id) {
    if (!state.boards.some(b => b.id === id)) return;
    state.boardId = id; state.selected.clear(); $("#sidebar").classList.remove("open"); renderAll();
  }

  // ---- Welcome card -------------------------------------------------------------------------
  let welcome;
  function buildWelcome() {
    welcome = document.createElement("aside");
    welcome.className = "welcome"; welcome.setAttribute("aria-label", "Welcome");
    const chips = [
      state.assets.some(a => (a.tags || []).includes("pixel art")) && ["Search “pixel art”", () => searchFor("pixel art")],
      state.boards.some(b => b.id === "board_rooms") && ["Open Rooms & Light", () => openBoard("board_rooms")],
      state.assets.some(a => (a.tags || []).includes("pink")) && ["Search “pink”", () => searchFor("pink")],
      state.canEdit && ["Pin your own", () => openAddImages("upload")],
    ].filter(Boolean);
    welcome.innerHTML = `<button class="welcome-close" type="button" aria-label="Close welcome">×</button><strong>Take a look around.</strong><span>Drag, zoom, or double-click a picture.</span><div class="welcome-chips"></div>`;
    const row = welcome.querySelector(".welcome-chips");
    for (const [label, run] of chips) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = label;
      b.addEventListener("click", () => { run(); close(); }); row.append(b);
    }
    const close = () => { welcome.hidden = true; viewport.classList.remove("has-welcome"); store.set("vb-welcome-v1", "1"); };
    welcome.querySelector(".welcome-close").addEventListener("click", close);
    for (const ev of ["pointerdown", "wheel"]) welcome.addEventListener(ev, e => e.stopPropagation());
    viewport.append(welcome); viewport.classList.add("has-welcome");
    // Once they start moving around the board, the card has done its job: fade it out and keep it away.
    const settle = () => { welcome.classList.add("leaving"); store.set("vb-welcome-v1", "1"); setTimeout(() => { welcome.hidden = true; viewport.classList.remove("has-welcome"); }, 500); };
    setTimeout(() => { for (const ev of ["wheel", "pointerdown"]) viewport.addEventListener(ev, settle, { once: true, passive: true }); }, 1200);
  }
  function maybeWelcome() {
    if (welcome || store.get("vb-welcome-v1") || !state.assets.length) return;
    const justLoaded = store.get("vb-sample-just-loaded") === "1";
    if (!(state.capabilities?.demo || justLoaded)) return;
    store.set("vb-sample-just-loaded", "0");
    buildWelcome();
  }
  $("#empty-sample")?.addEventListener("click", () => { store.set("vb-welcome-v1", ""); store.set("vb-sample-just-loaded", "1"); });

  // ---- Viewer ----------------------------------------------------------------------------------
  const lb = document.createElement("div");
  lb.className = "lb"; lb.id = "lb"; lb.setAttribute("role", "dialog"); lb.setAttribute("aria-modal", "true"); lb.setAttribute("aria-label", "Picture viewer"); lb.setAttribute("aria-hidden", "true");
  lb.innerHTML = `<button class="lb-close" type="button" aria-label="Close viewer">×</button>
    <div class="lb-stage"><button class="lb-nav lb-prev" type="button" aria-label="Previous picture">‹</button><img alt=""><button class="lb-nav lb-next" type="button" aria-label="Next picture">›</button></div>
    <aside class="lb-side"><div class="lb-meta"></div><h2></h2><div class="lb-tags"></div><div class="lb-actions"></div><p class="lb-credit"></p></aside>`;
  document.body.append(lb);
  const img = lb.querySelector("img"), title = lb.querySelector("h2"), meta = lb.querySelector(".lb-meta"),
    tags = lb.querySelector(".lb-tags"), actions = lb.querySelector(".lb-actions"), credit = lb.querySelector(".lb-credit");
  let list = [], at = 0, opener = null;

  function show(index) {
    at = (index + list.length) % list.length;
    const a = state.assets.find(x => x.id === list[at]); if (!a) return;
    img.style.opacity = "0";
    img.onload = () => { img.style.opacity = "1"; };
    img.src = `/api/assets/${encodeURIComponent(a.id)}`; img.alt = a.alt || a.title || "";
    title.textContent = a.title || "Untitled";
    meta.textContent = [a.collection, `${at + 1} of ${list.length}`].filter(Boolean).join("  ·  ");
    tags.innerHTML = "";
    for (const tag of a.tags || []) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = tag;
      b.addEventListener("click", () => { closeViewer(); searchFor(tag); }); tags.append(b);
    }
    actions.innerHTML = "";
    const board = state.boards.find(b => b.id === state.boardId);
    if (state.canEdit && board && board.id !== "main") {
      const pin = document.createElement("button"); pin.type = "button"; pin.className = "primary"; pin.textContent = `Pin to ${board.name}`;
      pin.addEventListener("click", async () => { await addLibraryAsset(a.id); closeViewer(); }); actions.append(pin);
    }
    credit.textContent = a.credit || "";
    lb.querySelectorAll(".lb-nav").forEach(b => { b.hidden = list.length < 2; });
  }
  function openViewer(assetId, ids) {
    list = ids.length ? ids : [assetId]; opener = document.activeElement;
    lb.classList.add("open"); lb.setAttribute("aria-hidden", "false");
    show(Math.max(0, list.indexOf(assetId)));
    lb.querySelector(".lb-close").focus({ preventScroll: true });
  }
  function closeViewer() {
    if (!lb.classList.contains("open")) return;
    lb.classList.remove("open"); lb.setAttribute("aria-hidden", "true");
    try { opener?.focus?.({ preventScroll: true }); } catch {}
  }
  lb.querySelector(".lb-close").addEventListener("click", closeViewer);
  lb.querySelector(".lb-prev").addEventListener("click", () => show(at - 1));
  lb.querySelector(".lb-next").addEventListener("click", () => show(at + 1));
  lb.addEventListener("click", e => { if (e.target === lb || e.target.classList.contains("lb-stage")) closeViewer(); });
  document.addEventListener("keydown", e => {
    if (!lb.classList.contains("open")) return;
    e.stopPropagation();
    if (e.key === "Escape") closeViewer();
    else if (e.key === "ArrowRight") show(at + 1);
    else if (e.key === "ArrowLeft") show(at - 1);
    else if (e.key === "Tab") { e.preventDefault(); lb.querySelector(".lb-close").focus(); }
  }, true);

  // Double-click a picture on the canvas: open it large, with the other pictures on this board a keypress away.
  viewport.addEventListener("dblclick", e => {
    // Selecting re-draws the canvas between the two clicks, so find the picture by position, not by event target.
    const el = document.elementFromPoint(e.clientX, e.clientY)?.closest(".item.image"); if (!el) return;
    const mine = item(el.dataset.item); if (!mine?.asset_id) return;
    const ordered = currentItems().filter(i => i.kind === "image" && i.asset_id)
      .sort((p, q) => Math.round(p.state.y / 120) - Math.round(q.state.y / 120) || p.state.x - q.state.x).map(i => i.asset_id);
    openViewer(mine.asset_id, [...new Set(ordered)]);
  });
  // Search results: open large instead of the edit dialog.
  $("#visual-results")?.addEventListener("click", e => {
    const tile = e.target.closest(".visual-result"); if (!tile) return;
    e.stopPropagation(); e.preventDefault();
    const ids = [...$("#visual-results").querySelectorAll(".visual-result")].map(b => b.dataset.asset);
    openViewer(tile.dataset.asset, ids);
  }, true);

  // ---- Tools: show when some of them are scrolled out of sight ---------------------------------
  const toolbar = document.querySelector(".tools");
  if (toolbar) {
    const sync = () => {
      const room = toolbar.scrollWidth - toolbar.clientWidth;
      toolbar.classList.toggle("more-right", room > 2 && toolbar.scrollLeft < room - 2);
      toolbar.classList.toggle("more-left", toolbar.scrollLeft > 2);
    };
    toolbar.addEventListener("scroll", sync, { passive: true });
    addEventListener("resize", sync);
    if ("ResizeObserver" in window) new ResizeObserver(sync).observe(toolbar);
    sync();
  }

  // ---- Hooks ------------------------------------------------------------------------------------
  const baseRenderAll = renderAll;
  renderAll = function () { baseRenderAll(); maybeWelcome(); };
})();

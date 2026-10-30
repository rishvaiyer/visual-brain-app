const $ = (s) => document.querySelector(s);
const state = { key: "", canEdit: false, capabilities: {}, boards: [], folders: [], assets: [], fonts: [], items: [], trash: [], boardId: "main", selected: new Set(), tool: "select", drag: null, space: false, saveTimers: new Map(), libraryLimit: 80, trashLimit: 50, favoritesOnly: false, closedFolders: new Set(), visualQuery: "", semanticIds: new Set(), semanticAvailable: false, semanticPending: false, semanticChecked: false, semanticError: "", localVisualAvailable: false, localVisualMatches: null, localVisualPending: false, localVisualError: "", localVisualImageId: "", localVisualMethod: "" };
try { state.closedFolders = new Set(JSON.parse(localStorage.getItem("visual-brain-closed-folders") || "[]")); } catch {}
const viewport = $("#viewport"), stage = $("#stage");
const toastEl = $("#toast");
let toastTimer;

function toast(message) { toastEl.textContent = message; toastEl.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2600); }
function esc(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
async function request(path, method = "GET", body) {
  if (method !== "GET" && !state.canEdit) throw new Error("Sign in to edit this board");
  const response = await fetch(path, {method, headers: {"Content-Type": "application/json", "X-Board-Key": state.key}, body: body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function currentBoard() { return state.boards.find(b => b.id === state.boardId) || state.boards[0]; }
function currentItems() { return state.items.filter(i => i.board_id === state.boardId); }
function asset(id) { return state.assets.find(a => a.id === id); }
function item(id) { return state.items.find(i => i.id === id); }
function view() { return currentBoard().view; }
const loadedFontIds = new Set();
const loadingFontIds = new Set();
function loadFonts() {
  for (const font of state.fonts) {
    if (loadedFontIds.has(font.id) || loadingFontIds.has(font.id)) continue;
    loadingFontIds.add(font.id);
    try {
      const face = new FontFace(`VBFont_${font.id}`, `url(/api/fonts/${encodeURIComponent(font.id)})`);
      face.load().then(() => {
        document.fonts.add(face);
        loadedFontIds.add(font.id);
      }).catch(() => toast(`Could not load ${font.name}`)).finally(() => loadingFontIds.delete(font.id));
    } catch { loadingFontIds.delete(font.id); toast(`Could not load ${font.name}`); }
  }
}
function screenToWorld(clientX, clientY) { const box = viewport.getBoundingClientRect(), v = view(); return {x: (clientX - box.left - v.x) / v.zoom, y: (clientY - box.top - v.y) / v.zoom}; }
function showSaved(text = "Saved") { $("#save-status").textContent = text; }
function persistItem(i) { if (!state.canEdit) return; clearTimeout(state.saveTimers.get(i.id)); showSaved("Saving…"); const timer = setTimeout(async () => { try { await request(`/api/items/${i.id}`, "PUT", {state: i.state}); showSaved(); } catch (e) { showSaved("Save failed"); toast(e.message); } }, 350); state.saveTimers.set(i.id, timer); }
function persistView() { if (!state.canEdit) return; clearTimeout(state.viewTimer); const boardId=state.boardId, boardView={...view()}; state.viewTimer = setTimeout(async () => { try { await request(`/api/boards/${boardId}`, "PUT", {view: boardView}); } catch (e) { toast(e.message); } }, 450); }

function renderBoards() {
  const link = b => `<button class="board-link ${b.id === state.boardId ? "active" : ""}" data-board="${esc(b.id)}" title="${esc(b.name)}">${esc(b.name)}</button>`;
  const loose = state.boards.filter(b => !b.folder_id);
  $("#boards").innerHTML = loose.map(link).join("") + state.folders.map(f => `<div class="folder-group"><div class="folder-row"><button class="folder-toggle" data-toggle-folder="${esc(f.id)}" aria-expanded="${!state.closedFolders.has(f.id)}" title="${state.closedFolders.has(f.id) ? "Expand" : "Collapse"} folder">${state.closedFolders.has(f.id) ? "▸" : "▾"} <span>▤ ${esc(f.name)}</span></button><button class="folder-more" data-edit-folder="${esc(f.id)}" aria-label="Options for ${esc(f.name)}">···</button></div>${state.closedFolders.has(f.id) ? "" : `<div class="folder-boards">${state.boards.filter(b => b.folder_id === f.id).map(link).join("")}</div>`}</div>`).join("");
  $("#board-name").textContent = currentBoard().name;
  $("#boards").querySelectorAll("[data-board]").forEach(b => b.addEventListener("click", () => { state.boardId = b.dataset.board; state.selected.clear(); $("#sidebar").classList.remove("open"); renderAll(); }));
  $("#boards").querySelectorAll("[data-toggle-folder]").forEach(b => b.addEventListener("click", () => { const id=b.dataset.toggleFolder; state.closedFolders.has(id) ? state.closedFolders.delete(id) : state.closedFolders.add(id); localStorage.setItem("visual-brain-closed-folders",JSON.stringify([...state.closedFolders])); renderBoards(); }));
  $("#boards").querySelectorAll("[data-edit-folder]").forEach(b => b.addEventListener("click", () => openFolderDialog(b.dataset.editFolder)));
}
function renderLibrary() {
  const q = $("#search").value.toLowerCase().trim();
  const filter = $("#collection-filter"), selected = filter.value;
  const collections = [...new Set(state.assets.map(a=>a.collection).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
  filter.innerHTML = '<option value="">All collections</option>' + collections.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join("");
  filter.value = collections.includes(selected) ? selected : "";
  const matches = state.assets.filter(a => (!filter.value || a.collection === filter.value) && (!state.favoritesOnly || a.favorite) && [visualWords(a),a.source_url,a.image_url].filter(Boolean).join(" ").toLowerCase().includes(q));
  $("#asset-count").textContent = state.assets.length;
  $("#library").innerHTML = matches.length ? matches.slice(0,state.libraryLimit).map(a => `<div class="library-card" data-asset="${esc(a.id)}" draggable="true"><button class="library-tile" title="Add ${esc(assetDisplayLabel(a))} to board"><img src="/api/assets/${esc(a.id)}" alt="${esc(assetDisplayLabel(a))}" draggable="false" loading="lazy">${a.favorite ? '<span class="heart">♥</span>' : ''}</button><button class="library-options" title="Image options" aria-label="Options for ${esc(assetDisplayLabel(a))}">···</button></div>`).join("") + (matches.length > state.libraryLimit ? `<button id="library-more" class="library-more">Show more (${matches.length-state.libraryLimit} left)</button>` : "") : `<p class="library-empty">${q || filter.value || state.favoritesOnly ? "No images match these filters." : "Saved images appear here. Add an image to begin."}</p>`;
  $("#library").querySelectorAll(".library-card").forEach(card => { card.querySelector(".library-tile").addEventListener("click", () => addLibraryAsset(card.dataset.asset)); card.querySelector(".library-options").addEventListener("click",()=>openAssetDialog(card.dataset.asset)); card.addEventListener("dragstart", e=>dragOriginalImage(e,card.dataset.asset)); });
  $("#library-more")?.addEventListener("click",()=>{state.libraryLimit+=80;renderLibrary();});
}
function visualWords(a) { return [a.title, a.alt, a.collection, a.auto_text, ...(a.tags || []), ...(a.auto_tags || [])].filter(Boolean).join(" ").toLocaleLowerCase(); }
function assetDisplayLabel(a) {
  const usable = value => { const text=String(value||"").trim().replace(/\s+/g," "); return text && !/^(saved image|image|photo|untitled image)$/i.test(text) ? text : ""; };
  const direct=usable(a?.title)||usable(a?.alt);
  if(direct)return direct;
  const tags=[...(a?.tags||[]),...(a?.auto_tags||[])].map(usable).filter(Boolean);
  if(tags.length)return [...new Set(tags)].slice(0,2).join(" · ");
  const filename=String(a?.image_url||"").split(/[?#]/,1)[0].split("/").pop()||"";
  try { const name=decodeURIComponent(filename).replace(/\.(jpe?g|png|webp|gif|avif)$/i,"").replace(/[-_]+/g," ").trim(); if(name&&!/^img [a-f0-9]{8,}$/i.test(name)&&!/^[a-f0-9-]{20,}$/i.test(name))return name; } catch {}
  try { const host=new URL(a?.source_url).hostname.replace(/^www\./i,""); if(host)return `From ${host}`; } catch {}
  return "Saved image";
}
function renderVisualSearch() {
  const onEverything = state.boardId === "main";
  const panel = $("#visual-search"), results = $("#visual-results"), query = state.visualQuery.trim();
  panel.hidden = !onEverything;
  const active = onEverything && !!query;
  $("#visual-local").hidden = !active || !state.localVisualAvailable;
  $("#visual-local").disabled = state.localVisualPending;
  $("#visual-local").textContent = state.localVisualMatches ? "Refresh image matches" : "Find by image content";
  results.hidden = !active;
  viewport.classList.toggle("searching", active);
  $("#visual-clear").hidden = !query;
  if (!onEverything) return;
  const counts = new Map();
  for (const a of state.assets) for (const tag of [...(a.tags || []), ...(a.auto_tags || [])]) counts.set(tag, (counts.get(tag) || 0) + 1);
  const tags = [...counts].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0])).slice(0, 8);
  $("#visual-tags").innerHTML = tags.map(([tag]) => `<button type="button" data-tag="${esc(tag)}" aria-label="Search tag ${esc(tag)}">${esc(tag)}</button>`).join("");
  $("#visual-tags").querySelectorAll("[data-tag]").forEach(button => button.onclick = () => { $("#visual-query").value = button.dataset.tag; updateVisualQuery(); });
  if (!active) { $("#visual-search-status").textContent = state.assets.length ? `${state.assets.length} image${state.assets.length === 1 ? "" : "s"} in your library` : "Add images to start searching"; return; }
  const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const byId = new Map(state.assets.map(a => [a.id, a]));
  const matches = state.localVisualMatches ? state.localVisualMatches.map(m => byId.get(m.id)).filter(Boolean) : state.assets.filter(a => terms.every(term => visualWords(a).includes(term)) || state.semanticIds.has(a.id));
  results.innerHTML = matches.length ? `<div class="visual-results-grid">${matches.map(a => `<button class="visual-result" data-asset="${esc(a.id)}" type="button" aria-label="Open ${esc(assetDisplayLabel(a))}"><img src="/api/assets/${esc(a.id)}" alt="${esc(assetDisplayLabel(a))}" loading="lazy"><span class="visual-result-caption">${esc(assetDisplayLabel(a))}</span></button>`).join("")}</div>` : `<div class="visual-no-results">No images match “${esc(query)}”. Add a tag or try another word.</div>`;
  results.querySelectorAll("[data-asset]").forEach(button => button.onclick = () => openAssetDialog(button.dataset.asset));
  const count = `${matches.length} of ${state.assets.length} image${state.assets.length === 1 ? "" : "s"}`;
  $("#visual-search-status").textContent = state.localVisualPending ? (state.localVisualImageId ? "Finding visually related images…" : "Searching image content…") : state.localVisualError ? state.localVisualError : state.localVisualMatches ? (state.localVisualImageId ? `${matches.length} related images · ${state.localVisualMethod||"visual search"}` : `${matches.length} visual suggestions, sorted by similarity · results may be imperfect`) : state.semanticPending ? `${count} · Checking theme…` : state.semanticError ? `${count} · ${state.semanticError}` : `${count} · Words, tags, and image labels`;
}
function updateVisualQuery() {
  state.visualQuery = $("#visual-query").value.trim();
  state.semanticIds.clear(); state.semanticError = ""; state.semanticPending = false; state.semanticChecked = false;
  state.localVisualMatches = null; state.localVisualPending = false; state.localVisualError = ""; state.localVisualImageId = ""; state.localVisualMethod = "";
  renderVisualSearch();
}
async function checkLocalVisual() {
  if (!state.visualQuery || (!state.localVisualAvailable && !state.localVisualImageId) || state.localVisualPending) return;
  const query = state.visualQuery, imageId = state.localVisualImageId;
  state.semanticIds.clear(); state.semanticChecked = false;
  state.localVisualPending = true; state.localVisualError = ""; renderVisualSearch();
  try {
    const result = state.localVisualImageId ? await request("/api/similar-images", "POST", {asset_id:state.localVisualImageId}) : await request("/api/local-visual-search", "POST", {query});
    if (query !== state.visualQuery || imageId !== state.localVisualImageId) return;
    state.localVisualMatches = result.matches || [];
    state.localVisualMethod = result.method || "local visual search";
  } catch (e) { if (query === state.visualQuery && imageId === state.localVisualImageId) state.localVisualError = "Image content search unavailable"; }
  finally { if (query === state.visualQuery && imageId === state.localVisualImageId) { state.localVisualPending = false; renderVisualSearch(); } }
}
function findSimilarImages(assetId) {
  const a=asset(assetId);if(!a)return;
  state.boardId="main";state.selected.clear();state.localVisualImageId=assetId;state.localVisualMatches=null;state.localVisualMethod="";state.localVisualError="";state.localVisualPending=false;
  state.semanticIds.clear();state.semanticChecked=false;state.semanticError="";state.semanticPending=false;
  state.visualQuery=`Similar to ${assetDisplayLabel(a)}`.slice(0,120);$("#visual-query").value=state.visualQuery;
  if($("#asset-dialog").open)$("#asset-dialog").close();renderAll();checkLocalVisual();
}
function originalFileName(a) { const ext={"image/jpeg":"jpg","image/png":"png","image/webp":"webp","image/gif":"gif","image/avif":"avif"}[a.mime]||"png"; return `${String(a.title||a.alt||"Saved image").replace(/[\\/:*?"<>|]/g,"-").replace(/\.(jpe?g|png|webp|gif|avif)$/i,"").trim().slice(0,80)||"Saved image"}.${ext}`; }
function dragOriginalImage(event, assetId) { const a=asset(assetId); if(!a)return; const url=new URL(`/api/assets/${a.id}`,location.href).href; event.dataTransfer.setData("DownloadURL",`${a.mime}:${originalFileName(a)}:${url}`);event.dataTransfer.effectAllowed="copy"; }
function renderTrash() {
  $("#trash-count").textContent = state.trash.length;
  $("#trash-search").hidden = state.trash.length < 12;
  const q=$("#trash-search").value.trim().toLowerCase(),matches=state.trash.filter(a=>(a.title||"Saved image").toLowerCase().includes(q));
  $("#trash-list").innerHTML = matches.length ? matches.slice(0,state.trashLimit).map(a=>`<div class="trash-entry"><img src="/api/assets/${esc(a.id)}" alt=""><span title="${esc(a.title || "Saved image")}">${esc(a.title || "Saved image")}</span><button data-restore="${esc(a.id)}">Restore</button></div>`).join("")+(matches.length>state.trashLimit?`<button id="trash-more" class="library-more">Show more (${matches.length-state.trashLimit} left)</button>`:"") : `<p class="trash-empty">${state.trash.length?"No images match your search.":"Trash is empty."}</p>`;
  $("#restore-all").disabled = $("#empty-trash").disabled = !state.trash.length;
  $("#trash-list").querySelectorAll("[data-restore]").forEach(button=>button.addEventListener("click",async()=>{try{await request(`/api/assets/${button.dataset.restore}/restore`,"POST",{});await refreshData();toast("Image restored to Library and boards");}catch(e){toast(e.message);}}));
  $("#trash-more")?.addEventListener("click",()=>{state.trashLimit+=50;renderTrash();});
}
function imageMarkup(i) {
  return `<div class="image-clip"><img src="/api/assets/${i.asset_id}" alt="${esc(assetDisplayLabel(asset(i.asset_id)))}" draggable="false"></div>`;
}
function safeColor(value, fallback) { return /^#[0-9a-fA-F]{6}$/.test(value || "") ? value : fallback; }
function shapeMarkup(s) {
  const w = Math.max(1, s.w), h = Math.max(1, s.h), fill = safeColor(s.fill, "#c8d4bb"), stroke = safeColor(s.stroke, "#4d684f"), line = Math.max(1, Math.min(30, Number(s.strokeWidth) || 2));
  const inset = line / 2 + 1, shape = s.shape || "rectangle";
  let body;
  if (shape === "ellipse") body = `<ellipse cx="${w/2}" cy="${h/2}" rx="${Math.max(1,w/2-inset)}" ry="${Math.max(1,h/2-inset)}" fill="${fill}" stroke="${stroke}" stroke-width="${line}"/>`;
  else if (shape === "triangle") body = `<polygon points="${w/2},${inset} ${w-inset},${h-inset} ${inset},${h-inset}" fill="${fill}" stroke="${stroke}" stroke-width="${line}" stroke-linejoin="round"/>`;
  else if (shape === "line" || shape === "arrow") body = `<line x1="${inset}" y1="${h/2}" x2="${w-inset-(shape === "arrow" ? 12 : 0)}" y2="${h/2}" stroke="${stroke}" stroke-width="${line}" stroke-linecap="round"/>${shape === "arrow" ? `<polygon points="${w-inset},${h/2} ${w-inset-16},${h/2-11} ${w-inset-16},${h/2+11}" fill="${stroke}"/>` : ""}`;
  else body = `<rect x="${inset}" y="${inset}" width="${Math.max(1,w-2*inset)}" height="${Math.max(1,h-2*inset)}" rx="3" fill="${fill}" stroke="${stroke}" stroke-width="${line}"/>`;
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${body}</svg>`;
}
function fitCroppedImage(el) {
  const i = item(el.dataset.item), img = el.querySelector("img");
  if (!i || !img?.naturalWidth || !img?.naturalHeight) return;
  const c = i.state.crop || {x:0,y:0,w:1,h:1}, iw = img.naturalWidth, ih = img.naturalHeight;
  const cropW = Math.max(.01,c.w) * iw, cropH = Math.max(.01,c.h) * ih;
  const scale = Math.max(i.state.w/cropW, i.state.h/cropH);
  Object.assign(img.style, {width:`${iw*scale}px`,height:`${ih*scale}px`,left:`${(i.state.w-cropW*scale)/2-c.x*iw*scale}px`,top:`${(i.state.h-cropH*scale)/2-c.y*ih*scale}px`});
}
function itemMarkup(i) {
  const s = i.state, selected = state.selected.has(i.id);
  const shape = i.kind === "image" ? imageMarkup(i) : i.kind === "text" ? esc(s.text || "Text") : i.kind === "frame" ? esc(s.text || "Frame") : i.kind === "shape" ? shapeMarkup(s) : `<svg viewBox="0 0 ${Math.max(1,s.w)} ${Math.max(1,s.h)}" preserveAspectRatio="none"><polyline points="${(s.points||[]).map(p=>`${p[0]},${p[1]}`).join(" ")}" fill="none" stroke="${esc(s.color || "#333b30")}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  return `<div class="item ${i.kind} ${selected ? "selected" : ""} ${s.locked ? "locked" : ""}" data-item="${i.id}" style="left:${s.x}px;top:${s.y}px;width:${s.w}px;height:${s.h}px;transform:rotate(${s.rotation || 0}deg);z-index:${s.z || 1}">${shape}${selected && !s.locked ? '<div class="resize-handle" data-handle="resize"></div>' : ''}</div>`;
}
function renderCanvas() {
  const v = view(); stage.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.zoom})`;
  const background = ["dots", "graph", "grid", "lined", "plain"].includes(v.background) ? v.background : "dots";
  viewport.dataset.background = background;
  viewport.style.setProperty("--paper-step", `${24 * v.zoom}px`);
  viewport.style.setProperty("--paper-major", `${120 * v.zoom}px`);
  viewport.style.setProperty("--paper-x", `${v.x}px`);
  viewport.style.setProperty("--paper-y", `${v.y}px`);
  $("#background-choice").value = background;
  stage.innerHTML = currentItems().map(itemMarkup).join("");
  stage.querySelectorAll(".item.text").forEach(el => { const s = item(el.dataset.item).state; Object.assign(el.style, {fontFamily: s.fontFamily || "Avenir Next, sans-serif", fontSize: `${Math.max(8, Math.min(200, Number(s.fontSize) || 22))}px`, fontWeight: s.fontWeight || "400", textAlign: s.textAlign || "left", color: safeColor(s.color, "#30382f")}); });
  stage.querySelectorAll(".item.image").forEach(el => { const img=el.querySelector("img"); if(img.complete) fitCroppedImage(el); else img.onload=()=>fitCroppedImage(el); });
  $("#empty").hidden = currentItems().length > 0;
  $("#zoom-label").textContent = `${Math.round(v.zoom * 100)}%`;
  viewport.classList.toggle("pan-mode", state.tool === "pan" || state.space);
  renderVisualSearch();
}
function field(label, id, value, opts = {}) { return `<label class="field">${label}<input id="${id}" type="${opts.type || "text"}" value="${esc(value)}" ${opts.placeholder !== undefined ? `placeholder="${esc(opts.placeholder)}"` : ""} ${opts.min !== undefined ? `min="${opts.min}"` : ""} ${opts.max !== undefined ? `max="${opts.max}"` : ""} ${opts.step !== undefined ? `step="${opts.step}"` : ""}></label>`; }
function selectField(label, id, value, options) { return `<label class="field">${label}<select id="${id}">${options.map(([v,t])=>`<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>`; }
function inspectorActions(i) { return `<div class="detail-section"><h3>Arrange</h3><div class="action-row"><button data-action="front">Bring forward</button><button data-action="back">Send backward</button><button data-action="duplicate">Duplicate</button><button data-action="lock">${i.state.locked ? "Unlock" : "Lock"}</button><button data-action="delete" class="danger">Delete from board</button>${i.asset_id ? '<button data-action="trash-asset" class="danger">Move image to Trash</button>' : ''}</div></div>`; }
function renderInspector() {
  const ids = [...state.selected], body = $("#inspector-body");
  $("#delete-selection").disabled = !ids.length;
  $("#inspector").classList.toggle("has-selection", !!ids.length);
  $("#app").classList.toggle("inspector-open", !!ids.length);
  if (!ids.length) { body.innerHTML = '<p class="muted">Select an image or object on the board.</p>'; return; }
  if (ids.length > 1) { body.innerHTML = `<p class="selection-count">${ids.length} objects selected</p><div class="action-row"><button data-action="group">Group</button><button data-action="delete-many" class="danger">Delete from board</button></div>`; wireActions(); return; }
  const i = item(ids[0]);
  if (!i) { state.selected.clear(); return renderInspector(); }
  const s = i.state, a = i.asset_id ? asset(i.asset_id) : null;
  if (!state.canEdit) {
    body.innerHTML = `${a ? `<img class="detail-image" src="/api/assets/${encodeURIComponent(a.id)}" alt="${esc(a.alt||"Selected image")}">` : ""}
      <p>${esc(a?.title || s.text || i.kind)}</p>
      ${a?.collection ? `<p class="muted">${esc(a.collection)}</p>` : ""}
      ${a?.tags?.length ? `<p class="muted">${esc(a.tags.join(" · "))}</p>` : ""}
      ${a?.source_url && /^https?:\/\//i.test(a.source_url) ? `<a class="source-link" href="${esc(a.source_url)}" target="_blank" rel="noopener noreferrer">Open source page ↗</a>` : ""}`;
    return;
  }
  let html = a ? `<img class="detail-image" src="/api/assets/${a.id}" alt="${esc(a.alt || "Selected image")}" draggable="true" title="Drag into a Finder folder to copy the original"><div class="image-save-row"><button id="save-original" type="button">Save original as…</button><span>or drag the image into Finder</span></div>` : "";
  if (a) html += `${a.pixel_width ? `<p class="muted">Original: ${a.pixel_width} × ${a.pixel_height} px</p>` : ""}${field("Title", "asset-title", a.title || "", {placeholder:assetDisplayLabel(a)})}${field("Collection", "asset-collection", a.collection || "")}${field("Tags (comma separated)", "asset-tags", (a.tags || []).join(", "))}${a.auto_tags?.length ? `<p class="muted">Image labels: ${esc(a.auto_tags.join(" · "))}</p>` : ""}<label class="field"><input id="asset-favorite" type="checkbox" ${a.favorite ? "checked" : ""}> Favorite</label>${a.source_url ? `<a class="source-link" href="${esc(a.source_url)}" target="_blank" rel="noopener noreferrer">Open source page ↗</a>` : ""}<div class="detail-section"><h3>Find related images</h3><div class="action-row"><button data-action="find-similar" type="button">Find similar images</button></div></div>`;
  if (i.kind === "text" || i.kind === "frame") html += `<label class="field">Text<textarea id="item-text">${esc(s.text || "")}</textarea></label>`;
  if (i.kind === "text") {
    const fonts=["Avenir Next, sans-serif","Helvetica Neue, sans-serif","Georgia, serif","Baskerville, serif","Menlo, monospace"];
    const saved=state.fonts.map(font=>[`VBFont_${font.id}`,font.name]);
    const choices=[[fonts[0],"Avenir Next"],[fonts[1],"Helvetica Neue"],[fonts[2],"Georgia"],[fonts[3],"Baskerville"],[fonts[4],"Menlo"],...saved,["custom","My font…"]];
    const known=choices.some(([value])=>value===s.fontFamily && value!=="custom");
    html += `<div class="detail-section"><h3>Typography</h3>${selectField("Font", "text-font-preset", known ? s.fontFamily : s.fontFamily ? "custom" : fonts[0], choices)}${field("Font installed on this device","text-font-custom",s.fontFamily && !known ? s.fontFamily : "")}<div class="field-row">${field("Size", "text-font-size", s.fontSize || 22,{type:"number",min:8,max:200})}${field("Color", "text-color", safeColor(s.color,"#30382f"),{type:"color"})}</div><div class="field-row">${selectField("Weight","text-weight",s.fontWeight || "400",[["400","Regular"],["600","Semibold"],["700","Bold"]])}${selectField("Align","text-align",s.textAlign || "left",[["left","Left"],["center","Center"],["right","Right"]])}</div></div>`;
  }
  if (i.kind === "shape") html += `<div class="detail-section"><h3>Shape</h3>${selectField("Type","shape-type",s.shape || "rectangle",[["rectangle","Rectangle"],["ellipse","Ellipse"],["triangle","Triangle"],["line","Line"],["arrow","Arrow"]])}<div class="field-row">${field("Fill","shape-fill",safeColor(s.fill,"#c8d4bb"),{type:"color"})}${field("Outline","shape-stroke",safeColor(s.stroke,"#4d684f"),{type:"color"})}</div>${field("Outline width","shape-stroke-width",s.strokeWidth || 2,{type:"number",min:1,max:30})}</div>`;
  html += `<div class="detail-section"><h3>Position & size</h3><div class="field-row">${field("X", "item-x", Math.round(s.x), {type:"number"})}${field("Y", "item-y", Math.round(s.y), {type:"number"})}${field("Width", "item-w", Math.round(s.w), {type:"number",min:40})}${field("Height", "item-h", Math.round(s.h), {type:"number",min:40})}</div>${field("Rotation", "item-rotation", s.rotation || 0, {type:"number",min:-360,max:360})}</div>`;
  if (a) { const c = s.crop || {x:0,y:0,w:1,h:1}; html += `<div class="detail-section"><h3>Crop (original is preserved)</h3>${["x","y","w","h"].map(k=>field(({x:"Left",y:"Top",w:"Width",h:"Height"})[k], `crop-${k}`, c[k], {type:"range",min:0,max:1,step:.01})).join("")}<div class="action-row"><button data-action="reset-crop">Reset crop</button></div></div>`; }
  html += inspectorActions(i); body.innerHTML = html;
  for (const k of ["x","y","w","h","rotation"]) { const el = $(`#item-${k}`); el.addEventListener("change", () => { s[k] = k === "w" || k === "h" ? Math.max(40, Number(el.value)) : Number(el.value); persistItem(i); renderCanvas(); }); }
  if (a) {
    $(".detail-image").addEventListener("dragstart", e=>dragOriginalImage(e,a.id));
    $("#save-original").addEventListener("click",()=>saveOriginalImage(a.id));
    for (const [id,key] of [["asset-collection","collection"],["asset-tags","tags"]]) $("#"+id).addEventListener("change", async e => { a[key] = key === "tags" ? e.target.value.split(",").map(t=>t.trim()).filter(Boolean) : e.target.value; await saveAsset(a); });
    $("#asset-title").addEventListener("change", async e => { a.title = e.target.value; await saveAsset(a); });
    $("#asset-favorite").addEventListener("change", async e => { a.favorite = e.target.checked; await saveAsset(a); });
    for (const k of ["x","y","w","h"]) $("#crop-"+k).addEventListener("input", e => { const c = s.crop ||= {x:0,y:0,w:1,h:1}; c[k] = Number(e.target.value); c.w = Math.max(.05, Math.min(c.w, 1-c.x)); c.h = Math.max(.05, Math.min(c.h, 1-c.y)); c.x = Math.min(c.x, 1-c.w); c.y = Math.min(c.y, 1-c.h); persistItem(i); renderCanvas(); });
  }
  if (i.kind === "text" || i.kind === "frame") $("#item-text").addEventListener("input", e => { s.text = e.target.value; persistItem(i); renderCanvas(); });
  if (i.kind === "text") {
    $("#text-font-preset").addEventListener("change", e => { if(e.target.value !== "custom") { s.fontFamily=e.target.value; $("#text-font-custom").value=""; persistItem(i); renderCanvas(); } else $("#text-font-custom").focus(); });
    $("#text-font-custom").addEventListener("change", e => { if(e.target.value.trim()) { s.fontFamily=e.target.value.trim().slice(0,100); persistItem(i); renderCanvas(); } });
    for (const [id,key] of [["text-font-size","fontSize"],["text-color","color"],["text-weight","fontWeight"],["text-align","textAlign"]]) $("#"+id).addEventListener("input", e => { s[key] = key === "fontSize" ? Math.max(8,Math.min(200,Number(e.target.value)||22)) : e.target.value; persistItem(i); renderCanvas(); });
  }
  if (i.kind === "shape") for (const [id,key] of [["shape-type","shape"],["shape-fill","fill"],["shape-stroke","stroke"],["shape-stroke-width","strokeWidth"]]) $("#"+id).addEventListener("input", e => { s[key] = key === "strokeWidth" ? Math.max(1,Math.min(30,Number(e.target.value)||2)) : e.target.value; persistItem(i); renderCanvas(); });
  wireActions();
}
async function saveAsset(a) { try { await request(`/api/assets/${a.id}`, "PUT", {title:a.title,tags:a.tags,collection:a.collection,favorite:a.favorite}); renderLibrary(); showSaved(); } catch(e) { toast(e.message); } }
function wireActions() { $("#inspector-body").querySelectorAll("[data-action]").forEach(b => b.addEventListener("click", () => performAction(b.dataset.action))); }
async function performAction(action) {
  const chosen = [...state.selected].map(item).filter(Boolean);
  if (!chosen.length) return;
  try {
    if (action === "find-similar") { findSimilarImages(chosen[0].asset_id); return; }
    if (action === "delete" || action === "delete-many") { for (const i of chosen) { clearTimeout(state.saveTimers.get(i.id)); await request(`/api/items/${i.id}`, "DELETE"); state.items = state.items.filter(x=>x.id !== i.id); } state.selected.clear(); toast(`${chosen.length} removed from board. Saved images remain in Library.`); }
    else if (action === "trash-asset") { const i=chosen[0], a=asset(i.asset_id); if (!a || !confirm(`Move “${a.title || a.alt || "this image"}” to Trash? It will disappear from all boards. You can restore it from the sidebar.`)) return; await request(`/api/assets/${a.id}`,"DELETE");state.selected.clear();await refreshData();toast("Image moved to Trash. Restore it from the sidebar.");return; }
    else if (action === "group") { const group = `group_${crypto.randomUUID()}`; for (const i of chosen) { i.state.group = group; persistItem(i); } toast("Grouped. Drag one selected object to move the group."); }
    else if (action === "duplicate") { const i = chosen[0], copy = await request("/api/items", "POST", {board_id:state.boardId,kind:i.kind,asset_id:i.asset_id,state:{...structuredClone(i.state),x:i.state.x+24,y:i.state.y+24,z:maxZ()+1}}); state.items.push(copy); state.selected = new Set([copy.id]); }
    else if (action === "front" || action === "back") { const i = chosen[0]; i.state.z = action === "front" ? maxZ()+1 : Math.min(...currentItems().map(x=>x.state.z||1))-1; persistItem(i); }
    else if (action === "lock") { const i = chosen[0]; i.state.locked = !i.state.locked; persistItem(i); }
    else if (action === "reset-crop") { const i = chosen[0]; i.state.crop = {x:0,y:0,w:1,h:1}; persistItem(i); }
    renderAll();
  } catch(e) { toast(e.message); }
}
function maxZ() { return Math.max(0,...currentItems().map(i=>i.state.z||0)); }
function renderAll() { renderBoards(); renderLibrary(); renderTrash(); renderCanvas(); renderInspector(); }
async function refreshData() { const data=await request("/api/bootstrap"); Object.assign(state,{boards:data.boards,folders:data.folders||[],assets:data.assets,fonts:data.fonts||[],items:data.items,trash:data.trash||[]});if(!state.boards.some(b=>b.id===state.boardId)){state.boardId="main";state.selected.clear();}applyCapabilities(data);loadFonts();renderAll(); }
function setTool(tool) { state.tool = tool; document.querySelectorAll(".tool").forEach(b=>{const selected=b.dataset.tool === tool;b.classList.toggle("active",selected);b.setAttribute("aria-pressed",String(selected));}); viewport.classList.toggle("pan-mode",tool === "pan"); }

async function addObject(kind, x, y) {
  const defaults = kind === "text" ? {w:240,h:84,text:"Double click to edit",fontFamily:"Avenir Next, sans-serif",fontSize:22,fontWeight:"400",textAlign:"left",color:"#30382f"} : kind === "shape" ? {w:220,h:150,shape:"rectangle",fill:"#c8d4bb",stroke:"#4d684f",strokeWidth:2} : {w:320,h:220,text:"Frame"};
  const added = await request("/api/items", "POST", {board_id:state.boardId,kind,state:{x,y,...defaults,rotation:0,z:maxZ()+1,locked:false}});
  state.items.push(added); state.selected = new Set([added.id]); setTool("select"); renderAll();
  if (kind === "text") $("#item-text")?.focus();
}
async function addLibraryAsset(assetId) {
  try { const preview = new Image(); preview.src=`/api/assets/${encodeURIComponent(assetId)}`; await preview.decode(); const h=Math.max(80,Math.min(520,Math.round(260*preview.naturalHeight/preview.naturalWidth))); const box = viewport.getBoundingClientRect(); const pos = screenToWorld(box.left+box.width/2-130,box.top+box.height/2-h/2); const added = await request("/api/items", "POST", {board_id:state.boardId,kind:"image",asset_id:assetId,state:{x:pos.x,y:pos.y,w:260,h,rotation:0,z:maxZ()+1,locked:false,crop:{x:0,y:0,w:1,h:1}}}); state.items.push(added); state.selected = new Set([added.id]); renderAll(); return true; } catch(e) { toast(e.message); return false; }
}
function readFile(file) { return new Promise((resolve,reject)=>{ const reader = new FileReader(); reader.onload=()=>resolve(reader.result); reader.onerror=()=>reject(reader.error); reader.readAsDataURL(file); }); }
async function uploadFiles(files) {
  const images = [...files].filter(f=>f.type.startsWith("image/"));
  if (!images.length) return toast("Choose an image file.");
  let addedCount = 0;
  for (const file of images) { try { const result = await request("/api/capture","POST",{image_data_url:await readFile(file),title:file.name,board_id:state.boardId}); if (!state.assets.some(a=>a.id===result.asset.id)) state.assets.unshift(result.asset); state.items.push(result.item); state.selected = new Set([result.item.id]); addedCount++; toast(result.duplicate ? "Image already saved; added to board" : "Image saved to your library"); } catch(e) { toast(`${file.name}: ${e.message}`); } }
  renderAll();
  if (addedCount) $("#add-images-dialog").close();
}
function acceptCapturedImage(result) {
  if (!state.assets.some(a=>a.id===result.asset.id))state.assets.unshift(result.asset);
  state.items.push(result.item);state.selected=new Set([result.item.id]);renderAll();
}
async function captureImageUrl(url, screenPoint) {
  const body={url,board_id:state.boardId};
  if(screenPoint){const pos=screenToWorld(screenPoint.x,screenPoint.y);body.position={center_x:pos.x,center_y:pos.y};}
  const result=await request("/api/capture-url","POST",body);
  acceptCapturedImage(result);
  return result;
}
function extractDroppedImageUrl(dataTransfer) {
  const parse=value=>{
    for(const line of String(value||"").split(/\r?\n/)){const candidate=line.trim();if(!candidate||candidate.startsWith("#"))continue;try{const url=new URL(candidate);if(["http:","https:"].includes(url.protocol)&&!url.username&&!url.password)return url.href;}catch{}}
    return "";
  };
  const html=dataTransfer.getData("text/html");
  if(html){const doc=new DOMParser().parseFromString(html,"text/html");const image=doc.querySelector("img[src]");const imageUrl=parse(image?.getAttribute("src"));if(imageUrl)return imageUrl;const linkUrl=parse(doc.querySelector("a[href]")?.getAttribute("href"));if(linkUrl)return linkUrl;}
  return parse(dataTransfer.getData("text/uri-list"))||parse(dataTransfer.getData("text/plain"));
}
function setAddImagesTab(tab) {
  for (const name of ["upload","saved","url"]) {
    const selected = name === tab;
    $(`#add-tab-${name}`).setAttribute("aria-selected", String(selected));
    $(`#add-panel-${name}`).hidden = !selected;
  }
  if (tab === "saved") renderAddSavedImages();
  if (tab === "url") setTimeout(()=>$("#add-image-url").focus(), 0);
}
function openAddImages(tab = "upload") {
  const boardName = currentBoard()?.name || "this board";
  $("#add-images-board-upload").textContent = boardName;
  $("#add-images-board-url").textContent = boardName;
  $("#add-image-url-status").textContent = "";
  $("#add-images-dialog").showModal();
  setAddImagesTab(tab);
}
function renderAddSavedImages() {
  const query = $("#add-saved-search").value.trim().toLocaleLowerCase();
  const collectionSelect = $("#add-saved-collection"), selected = collectionSelect.value;
  const collections = [...new Set(state.assets.map(a=>a.collection).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
  collectionSelect.innerHTML = '<option value="">All collections</option>' + collections.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join("");
  collectionSelect.value = collections.includes(selected) ? selected : "";
  const favoritesOnly = $("#add-saved-favorites").getAttribute("aria-pressed") === "true";
  const matches = state.assets.filter(a => (!collectionSelect.value || a.collection === collectionSelect.value) && (!favoritesOnly || a.favorite) && (!query || [visualWords(a),a.source_url,a.image_url].filter(Boolean).join(" ").toLocaleLowerCase().includes(query)));
  $("#add-saved-count").textContent = `${matches.length} saved image${matches.length === 1 ? "" : "s"}${matches.length > 80 ? ` · showing 80 of ${matches.length}` : ""}`;
  const grid = $("#add-saved-grid");
  grid.innerHTML = matches.length ? matches.slice(0,80).map(a=>`<button type="button" class="add-saved-image" data-add-asset="${esc(a.id)}" title="Add ${esc(assetDisplayLabel(a))} to this board"><img src="/api/assets/${encodeURIComponent(a.id)}" alt="${esc(assetDisplayLabel(a))}" loading="lazy"><span><strong>${esc(assetDisplayLabel(a))}</strong><small>${esc(a.collection || (a.favorite ? "Favorite" : "Saved image"))}</small><em>Add to board</em></span></button>`).join("") : '<p class="add-saved-empty">No saved images match these filters.</p>';
  grid.querySelectorAll("[data-add-asset]").forEach(button=>button.addEventListener("click",async()=>{button.disabled=true;if(await addLibraryAsset(button.dataset.addAsset))$("#add-images-dialog").close();else button.disabled=false;}));
}
function fitContent() {
  const all = currentItems(); if (!all.length) { Object.assign(view(),{x:0,y:0,zoom:1}); renderCanvas(); persistView(); return; }
  const minX=Math.min(...all.map(i=>i.state.x)),minY=Math.min(...all.map(i=>i.state.y)),maxX=Math.max(...all.map(i=>i.state.x+i.state.w)),maxY=Math.max(...all.map(i=>i.state.y+i.state.h));
  const zoom=Math.min(2,Math.max(.1,Math.min((viewport.clientWidth-100)/(maxX-minX),(viewport.clientHeight-100)/(maxY-minY))));
  Object.assign(view(),{zoom,x:(viewport.clientWidth-(minX+maxX)*zoom)/2,y:(viewport.clientHeight-(minY+maxY)*zoom)/2}); renderCanvas(); persistView();
}
function zoomAt(factor,cx,cy) { const v=view(), pos=screenToWorld(cx,cy), box=viewport.getBoundingClientRect(); v.zoom=Math.max(.08,Math.min(8,v.zoom*factor)); v.x=cx-box.left-pos.x*v.zoom; v.y=cy-box.top-pos.y*v.zoom; renderCanvas(); persistView(); }

viewport.addEventListener("wheel", e=>{ e.preventDefault(); if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY*.007),e.clientX,e.clientY); else { view().x-=e.deltaX; view().y-=e.deltaY; renderCanvas(); persistView(); } },{passive:false});
viewport.addEventListener("pointerdown", e=>{
  if (e.button !== 0 && e.button !== 1) return;
  const target=e.target.closest(".item"), pos=screenToWorld(e.clientX,e.clientY);
  if (state.tool === "pan" || state.space || e.button === 1) { state.drag={mode:"pan",sx:e.clientX,sy:e.clientY,x:view().x,y:view().y}; viewport.classList.add("panning"); return; }
  if (state.tool === "text" || state.tool === "frame" || state.tool === "shape") { addObject(state.tool,pos.x,pos.y).catch(err=>toast(err.message)); return; }
  if (state.tool === "draw") { state.drag={mode:"draw",start:pos,points:[[0,0]],end:pos}; return; }
  if (target) {
    const i=item(target.dataset.item); if (!i) return;
    if (e.shiftKey) state.selected.has(i.id) ? state.selected.delete(i.id) : state.selected.add(i.id);
    else if (!state.selected.has(i.id)) state.selected = new Set([i.id]);
    if (!state.canEdit) { state.drag=null; renderCanvas(); renderInspector(); return; }
    const ids = i.state.group ? currentItems().filter(x=>x.state.group===i.state.group).map(x=>x.id) : [...state.selected];
    state.drag={mode:e.target.dataset.handle === "resize" ? "resize" : "move",start:pos,items:ids.map(id=>({i:item(id),x:item(id).state.x,y:item(id).state.y,w:item(id).state.w,h:item(id).state.h}))};
    if (i.state.locked) state.drag=null;
    renderCanvas(); renderInspector(); return;
  }
  if (!e.shiftKey) state.selected.clear();
  state.drag={mode:"marquee",start:pos,screenX:e.clientX,screenY:e.clientY,end:pos}; renderCanvas(); renderInspector();
});
window.addEventListener("pointermove",e=>{
  const d=state.drag; if (!d) return;
  if (d.mode === "pan") { view().x=d.x+e.clientX-d.sx; view().y=d.y+e.clientY-d.sy; renderCanvas(); return; }
  const pos=screenToWorld(e.clientX,e.clientY);
  if (d.mode === "move" || d.mode === "resize") { const dx=pos.x-d.start.x,dy=pos.y-d.start.y; for (const {i,x,y,w,h} of d.items) { if (i.state.locked) continue; if(d.mode==="move") { i.state.x=x+dx; i.state.y=y+dy; } else { i.state.w=Math.max(40,w+dx); i.state.h=Math.max(40,h+dy); } } renderCanvas(); return; }
  if (d.mode === "marquee") { d.end=pos; const b=viewport.getBoundingClientRect(), x=Math.min(d.screenX,e.clientX)-b.left,y=Math.min(d.screenY,e.clientY)-b.top; Object.assign($("#marquee").style,{left:x+"px",top:y+"px",width:Math.abs(e.clientX-d.screenX)+"px",height:Math.abs(e.clientY-d.screenY)+"px"}); $("#marquee").hidden=false; }
  if (d.mode === "draw") { d.end=pos; d.points.push([pos.x-d.start.x,pos.y-d.start.y]); }
});
window.addEventListener("pointerup",async()=>{
  const d=state.drag; if (!d) return; state.drag=null; viewport.classList.remove("panning");
  if (d.mode === "pan") persistView();
  else if (d.mode === "move" || d.mode === "resize") { for (const {i} of d.items) persistItem(i); renderInspector(); }
  else if (d.mode === "marquee") { $("#marquee").hidden=true; const left=Math.min(d.start.x,d.end.x),right=Math.max(d.start.x,d.end.x),top=Math.min(d.start.y,d.end.y),bottom=Math.max(d.start.y,d.end.y); if(right-left>4 && bottom-top>4) for (const i of currentItems()) { const s=i.state; if(s.x<right&&s.x+s.w>left&&s.y<bottom&&s.y+s.h>top) state.selected.add(i.id); } renderCanvas(); renderInspector(); }
  else if (d.mode === "draw" && d.points.length>2) { const minX=Math.min(0,...d.points.map(p=>p[0])),minY=Math.min(0,...d.points.map(p=>p[1])),maxX=Math.max(...d.points.map(p=>p[0])),maxY=Math.max(...d.points.map(p=>p[1])); try { const added=await request("/api/items","POST",{board_id:state.boardId,kind:"drawing",state:{x:d.start.x+minX,y:d.start.y+minY,w:Math.max(1,maxX-minX),h:Math.max(1,maxY-minY),points:d.points.map(p=>[p[0]-minX,p[1]-minY]),z:maxZ()+1,color:"#333b30"}}); state.items.push(added); renderCanvas(); } catch(e) { toast(e.message); } }
});
viewport.addEventListener("dblclick",e=>{ const i=item(e.target.closest(".item")?.dataset.item); if(i?.kind === "text" || i?.kind === "frame") { state.selected=new Set([i.id]); renderAll(); $("#item-text")?.focus(); } });
viewport.addEventListener("dragenter",e=>{const types=[...(e.dataTransfer?.types||[])];if(!types.includes("Files")&&(types.includes("text/uri-list")||types.includes("text/html")||types.includes("text/plain")))viewport.classList.add("url-drop-target");});
viewport.addEventListener("dragover",e=>{e.preventDefault();e.dataTransfer.dropEffect="copy";const types=[...(e.dataTransfer?.types||[])];viewport.classList.toggle("url-drop-target",!types.includes("Files")&&(types.includes("text/uri-list")||types.includes("text/html")||types.includes("text/plain")));});
viewport.addEventListener("dragleave",e=>{if(e.target===viewport)viewport.classList.remove("url-drop-target");});
viewport.addEventListener("drop",e=>{e.preventDefault();viewport.classList.remove("url-drop-target");if(e.dataTransfer.files?.length){uploadFiles(e.dataTransfer.files);return;}const url=extractDroppedImageUrl(e.dataTransfer);if(!url){toast("Drop an image or a direct image URL.");return;}captureImageUrl(url,{x:e.clientX,y:e.clientY}).then(result=>toast(result.duplicate?"Image already saved at drop location":"Image saved to Library and board")).catch(error=>toast(error.message));});
document.addEventListener("paste",e=>{ if (["INPUT","TEXTAREA"].includes(document.activeElement.tagName)) return; const files=[...e.clipboardData.items].filter(i=>i.kind==="file").map(i=>i.getAsFile()).filter(Boolean); if(files.length){e.preventDefault();uploadFiles(files);} });
document.addEventListener("keydown",e=>{
  if (["INPUT","TEXTAREA"].includes(document.activeElement.tagName)) return;
  if(e.code==="Space"){e.preventDefault();state.space=true;viewport.classList.add("pan-mode");}
  if(e.key==="Delete"||e.key==="Backspace") { if(state.selected.size){e.preventDefault();performAction("delete-many");} }
  const shortcuts={v:"select",h:"pan",t:"text",f:"frame",d:"draw"}; if(shortcuts[e.key?.toLowerCase()]) setTool(shortcuts[e.key.toLowerCase()]);
  if((e.metaKey||e.ctrlKey)&&e.key==="0"){e.preventDefault();fitContent();}
});
document.addEventListener("keyup",e=>{if(e.code==="Space"){state.space=false;viewport.classList.toggle("pan-mode",state.tool==="pan");}});
document.querySelectorAll(".tool").forEach(b=>b.addEventListener("click",()=>setTool(b.dataset.tool)));
$("#upload").onclick=$("#empty-upload").onclick=()=>openAddImages("upload");
$("#close-add-images").onclick=()=>$("#add-images-dialog").close();
document.querySelectorAll("[data-add-tab]").forEach(button=>button.addEventListener("click",()=>setAddImagesTab(button.dataset.addTab)));
$("#choose-image-files").onclick=()=>$("#file-input").click();
$("#add-saved-search").addEventListener("input",renderAddSavedImages);
$("#add-saved-collection").addEventListener("change",renderAddSavedImages);
$("#add-saved-favorites").addEventListener("click",e=>{const active=e.currentTarget.getAttribute("aria-pressed")!=="true";e.currentTarget.setAttribute("aria-pressed",String(active));e.currentTarget.setAttribute("aria-label",active?"Show all saved images":"Show favorites only");e.currentTarget.textContent=active?"♥":"♡";renderAddSavedImages();});
$("#add-image-url-form").addEventListener("submit",async e=>{
  e.preventDefault();
  const form=e.currentTarget, button=$("#add-image-url-submit"), status=$("#add-image-url-status"), url=$("#add-image-url").value.trim();
  if(!url)return;
  button.disabled=true;status.textContent="Fetching and saving a copy…";status.classList.remove("error");
  try {
    const result=await captureImageUrl(url);
    $("#add-images-dialog").close();form.reset();toast(result.duplicate?"Image already saved; added to board":"Image saved to your library and board");
  } catch(error) {status.textContent=error.message;status.classList.add("error");}
  finally {button.disabled=false;}
});
$("#file-input").addEventListener("change",e=>{uploadFiles(e.target.files);e.target.value="";});
$("#add-font").onclick=()=>$("#font-file-input").click();
$("#font-file-input").addEventListener("change",async e=>{
  const file=e.target.files[0];e.target.value="";if(!file)return;
  try {
    if(file.size>8*1024*1024) throw new Error("Font must be smaller than 8 MB");
    const result=await request("/api/fonts","POST",{name:file.name,font_data_url:await readFile(file)});
    state.boardId=result.board_id;state.selected=new Set([result.item_id]);await refreshData();
    toast(result.duplicate ? "Font already saved; new sample added" : "Font saved and ready to use");
  } catch(error) { toast(error.message); }
});
$("#fit").onclick=fitContent;
$("#background-choice").addEventListener("change", e => { view().background = e.target.value; renderCanvas(); persistView(); });
$("#delete-selection").onclick=()=>performAction("delete-many");
$("#search").addEventListener("input",()=>{state.libraryLimit=80;renderLibrary();});
$("#visual-query").addEventListener("input", updateVisualQuery);
$("#visual-local").addEventListener("click",checkLocalVisual);
$("#visual-clear").onclick = () => { $("#visual-query").value = ""; updateVisualQuery(); $("#visual-query").focus(); };
for (const el of [$("#visual-search"), $("#visual-results")]) { el.addEventListener("pointerdown", e => e.stopPropagation()); el.addEventListener("wheel", e => e.stopPropagation()); }
$(".paper-picker").addEventListener("pointerdown", e => e.stopPropagation());
$(".paper-picker").addEventListener("wheel", e => e.stopPropagation());
$("#collection-filter").addEventListener("change",()=>{state.libraryLimit=80;renderLibrary();});
$("#favorite-filter").addEventListener("click",()=>{state.favoritesOnly=!state.favoritesOnly;$("#favorite-filter").setAttribute("aria-pressed",String(state.favoritesOnly));$("#favorite-filter").textContent=state.favoritesOnly?"♥":"♡";state.libraryLimit=80;renderLibrary();});
$("#close-inspector").onclick=()=>{state.selected.clear();renderCanvas();renderInspector();};
$("#new-board").onclick=async()=>{const name=prompt("Board name");if(!name?.trim())return;try{const b=await request("/api/boards","POST",{name:name.trim()});state.boardId=b.id;state.selected.clear();await refreshData();window.dispatchEvent(new Event("visual-brain-boards-changed"));}catch(e){toast(e.message);}};
$("#new-folder").onclick=()=>openFolderDialog();
let editingFolderId=null;
function openFolderDialog(id=null) {
  editingFolderId=id;
  const folder=state.folders.find(f=>f.id===id);
  $("#folder-dialog h2").textContent=folder?"Folder options":"New folder";
  $("#folder-edit-name").value=folder?.name||"";
  $("#delete-folder").hidden=!folder;
  $("#folder-add-board").hidden=!folder;
  $("#folder-dialog").showModal();
  $("#folder-edit-name").focus();
}
$("#folder-form").addEventListener("submit",async e=>{e.preventDefault();try{const name=$("#folder-edit-name").value.trim();if(!name)return;const folder=await request(editingFolderId?`/api/folders/${editingFolderId}`:"/api/folders",editingFolderId?"PUT":"POST",{name});if(!editingFolderId)state.closedFolders.delete(folder.id);$("#folder-dialog").close();await refreshData();toast(editingFolderId?"Folder renamed":"Folder created");}catch(err){toast(err.message);}});
$("#cancel-folder").onclick=()=>$("#folder-dialog").close();
$("#delete-folder").onclick=async()=>{const folder=state.folders.find(f=>f.id===editingFolderId);if(!folder||!confirm(`Delete folder “${folder.name}”? Its boards will move to the main list.`))return;try{await request(`/api/folders/${folder.id}`,"DELETE");$("#folder-dialog").close();await refreshData();toast("Folder removed. Boards are still here.");}catch(err){toast(err.message);}};
$("#folder-add-board").onclick=async()=>{const folderId=editingFolderId,name=prompt("Board name");if(!name?.trim())return;try{const board=await request("/api/boards","POST",{name:name.trim(),folder_id:folderId});state.closedFolders.delete(folderId);state.boardId=board.id;$("#folder-dialog").close();await refreshData();window.dispatchEvent(new Event("visual-brain-boards-changed"));toast("Board created in folder");}catch(err){toast(err.message);}};
let editingAssetId=null, suggestionTimer=null;
function openAssetDialog(id){const a=asset(id);if(!a)return;editingAssetId=id;$("#asset-edit-preview").src=`/api/assets/${encodeURIComponent(id)}`;$("#asset-edit-title").value=a.title||"";$("#asset-edit-title").placeholder=assetDisplayLabel(a);$("#asset-edit-collection").value=a.collection||"";$("#asset-edit-tags").value=(a.tags||[]).join(", ");$("#asset-edit-favorite").checked=!!a.favorite;$("#collection-names").innerHTML=[...new Set(state.assets.map(x=>x.collection).filter(Boolean))].map(name=>`<option value="${esc(name)}"></option>`).join("");$("#asset-dialog").showModal();loadSuggestions(id);}
const suggestionBox=document.createElement("section");
suggestionBox.id="asset-suggestions";suggestionBox.className="asset-suggestions";suggestionBox.setAttribute("aria-live","polite");
$("#asset-edit-preview").insertAdjacentElement("afterend",suggestionBox);
$("#asset-dialog").addEventListener("close",()=>{clearTimeout(suggestionTimer);editingAssetId=null;});
function addSuggestedTag(tag){const input=$("#asset-edit-tags"),values=input.value.split(",").map(t=>t.trim()).filter(Boolean);if(!values.some(t=>t.toLowerCase()===tag.toLowerCase()))values.push(tag);input.value=values.join(", ");toast("Tag added. Save changes to keep it.");}
async function loadSuggestions(id){
  clearTimeout(suggestionTimer);
  if(!$("#asset-dialog").open||editingAssetId!==id)return;
  try{
    const result=await request(`/api/enrichment/${encodeURIComponent(id)}`);
    if(editingAssetId!==id||!$("#asset-dialog").open)return;
    if(result.status==="waiting"||result.status==="pending"||result.status==="running"){
      suggestionBox.innerHTML='<h3>Suggested tags</h3><p>Analyzing image…</p>';
      suggestionTimer=setTimeout(()=>loadSuggestions(id),800);return;
    }
    if(result.status==="error"){
      suggestionBox.innerHTML=`<h3>Suggested tags</h3><p>Image analysis failed: ${esc(result.error||"Unknown error")}</p><button class="suggestion-retry" type="button">Try again</button>`;
    }else{
      const tags=(result.suggestions?.tags||[]).filter(t=>t?.tag);
      const board=state.boards.find(b=>b.id===result.suggestions?.board_id);
      const collection=result.suggestions?.collection;
      suggestionBox.innerHTML=`<h3>Suggested tags</h3>${tags.length?`<div class="suggestion-list">${tags.map(t=>`<button type="button" data-suggested-tag="${esc(t.tag)}" title="${esc(t.source||"image")}">+ ${esc(t.tag)}</button>`).join("")}</div><p class="suggestion-source">Click a tag, then Save changes. Suggestions do not replace your tags.</p>`:'<p>No confident suggestions for this image.</p>'}${collection?`<p class="suggestion-board">Suggested collection: <button type="button" data-suggested-collection="${esc(collection)}">Use ${esc(collection)}</button></p>`:""}${board?`<p class="suggestion-board">Suggested board: <button type="button" data-suggested-board="${esc(board.id)}">Add to ${esc(board.name)}</button></p>`:""}<button class="suggestion-retry" type="button">Analyze again</button>`;
      suggestionBox.querySelectorAll("[data-suggested-tag]").forEach(button=>button.addEventListener("click",()=>addSuggestedTag(button.dataset.suggestedTag)));
      suggestionBox.querySelector("[data-suggested-collection]")?.addEventListener("click",event=>{$("#asset-edit-collection").value=event.currentTarget.dataset.suggestedCollection;toast("Collection selected. Save changes to keep it.");});
      suggestionBox.querySelector("[data-suggested-board]")?.addEventListener("click",async event=>{
        const boardId=event.currentTarget.dataset.suggestedBoard,a=asset(id);if(!a)return;
        const count=state.items.filter(i=>i.board_id===boardId).length;
        const h=Math.max(80,Math.min(520,Math.round(260*(a.pixel_height||1)/(a.pixel_width||1))));
        try{await request("/api/items","POST",{board_id:boardId,kind:"image",asset_id:id,state:{x:80+(count%5)*300,y:90+Math.floor(count/5)*330,w:260,h,rotation:0,z:count+1,locked:false,crop:{x:0,y:0,w:1,h:1}}});$("#asset-dialog").close();await refreshData();toast(`Added to ${state.boards.find(b=>b.id===boardId)?.name||"board"}`);}catch(error){toast(error.message);}
      });
    }
    suggestionBox.querySelector(".suggestion-retry")?.addEventListener("click",async()=>{try{await request(`/api/enrichment/${encodeURIComponent(id)}/retry`,"POST",{});loadSuggestions(id);}catch(error){toast(error.message);}});
  }catch(error){suggestionBox.innerHTML='<h3>Suggested tags</h3><p>Suggestions unavailable for this image.</p>';}
}
$("#asset-form").addEventListener("submit",async e=>{e.preventDefault();const a=asset(editingAssetId);if(!a)return;try{await request(`/api/assets/${a.id}`,"PUT",{title:$("#asset-edit-title").value.trim(),collection:$("#asset-edit-collection").value.trim(),tags:$("#asset-edit-tags").value.split(",").map(t=>t.trim()).filter(Boolean),favorite:$("#asset-edit-favorite").checked});$("#asset-dialog").close();await refreshData();toast("Image details saved");}catch(err){toast(err.message);}});
$("#asset-cancel").onclick=()=>$("#asset-dialog").close();
$("#asset-find-similar").onclick=()=>findSimilarImages(editingAssetId);
$("#asset-save-original").onclick=()=>saveOriginalImage(editingAssetId);
$("#asset-move-trash").onclick=async()=>{const a=asset(editingAssetId);if(!a||!confirm(`Move “${a.title||a.alt||"this image"}” to Trash? It will disappear from all boards until restored.`))return;try{await request(`/api/assets/${a.id}`,"DELETE");$("#asset-dialog").close();state.selected.clear();await refreshData();toast("Image moved to Trash");}catch(err){toast(err.message);}};
$("#board-options").onclick=()=>{const b=currentBoard();$("#board-edit-name").value=b.name;$("#board-edit-name").disabled=b.id==="main";$("#board-edit-folder").innerHTML='<option value="">No folder</option>'+state.folders.map(f=>`<option value="${esc(f.id)}">${esc(f.name)}</option>`).join("");$("#board-edit-folder").value=b.folder_id||"";$("#board-edit-folder").disabled=b.id==="main";$("#delete-board").hidden=b.id==="main";$("#board-dialog").showModal();};
$("#board-form").addEventListener("submit",async e=>{e.preventDefault();const b=currentBoard();try{await request(`/api/boards/${b.id}`,"PUT",{name:$("#board-edit-name").value.trim(),folder_id:$("#board-edit-folder").value||null});$("#board-dialog").close();await refreshData();window.dispatchEvent(new Event("visual-brain-boards-changed"));toast("Board updated");}catch(err){toast(err.message);}});
$("#cancel-board").onclick=()=>$("#board-dialog").close();
$("#delete-board").onclick=async()=>{const b=currentBoard();if(b.id==="main"||!confirm(`Delete board “${b.name}” and all objects on it? Saved images stay in Library. This cannot be undone.`))return;try{await request(`/api/boards/${b.id}`,"DELETE");state.boardId="main";state.selected.clear();$("#board-dialog").close();await refreshData();window.dispatchEvent(new Event("visual-brain-boards-changed"));toast("Board deleted. Saved images remain in Library.");}catch(err){toast(err.message);}};
$("#open-trash").onclick=()=>$("#trash-dialog").showModal();
$("#trash-search").addEventListener("input",()=>{state.trashLimit=50;renderTrash();});
$("#close-trash").onclick=()=>$("#trash-dialog").close();
$("#restore-all").onclick=async()=>{try{const result=await request("/api/trash/restore-all","POST",{});await refreshData();toast(`${result.restored} image${result.restored===1?"":"s"} restored`);}catch(err){toast(err.message);}};
$("#empty-trash").onclick=async()=>{const count=state.trash.length;if(!count||!confirm(`Permanently delete ${count} image${count===1?"":"s"} in Trash? Their original files and saved placements will be removed from ${state.capabilities.runtime==="hosted"?"this hosted Visual Brain":"this Mac"}. This cannot be undone.`))return;try{const result=await request("/api/trash","DELETE");await refreshData();toast(`${result.deleted} image${result.deleted===1?"":"s"} permanently removed.`);}catch(err){toast(err.message);}};
$("#save-as").onclick=()=>$("#save-dialog").showModal();
$("#close-save-dialog").onclick=()=>$("#save-dialog").close();
$("#backup").onclick=()=>saveAllData();
$("#save-all-data").onclick=()=>saveAllData();
$("#save-board-image").onclick=()=>saveBoardImage();
$("#pair").onclick=()=>{$("#pair-key").value=state.key;$("#pair-dialog").showModal();};
$("#copy-key").onclick=async()=>{await navigator.clipboard.writeText(state.key);toast("Pairing key copied");};
$("#sidebar-toggle").onclick=()=>$("#sidebar").classList.toggle("open");
$("#sidebar-close").onclick=()=>$("#sidebar").classList.remove("open");

function applyCapabilities(data) {
  const caps=data.capabilities || {runtime:data.key?"local":"hosted",extension_capture:!!data.key,local_visual_search:!!data.local_visual_search_available,similar_images:true,backup_export:true};
  state.capabilities=caps;
  const hosted=caps.runtime==="hosted";
  $("#pair").hidden=!caps.extension_capture;
  if (hosted) {
    $("#save-dialog-help").textContent="Your browser will download the file. Visual Brain keeps its own saved copy online.";
  }
  renderDemoBanner(caps);
  state.localVisualAvailable=!!caps.local_visual_search;
  $("#asset-find-similar").hidden=!caps.similar_images;
}
function renderDemoBanner(caps) {
  const el=$("#demo-banner"); if (!el) return;
  el.hidden=!caps.demo;
  if (!caps.demo) return;
  const when=caps.demo_reset_at ? new Date(caps.demo_reset_at) : null;
  const clock=when && !isNaN(when) ? when.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"}) : "";
  $("#demo-reset-time").textContent=clock ? `Everything here is wiped at ${clock}.` : "Everything here is wiped every few hours.";
}

async function init() {
  try {
    const data=await request("/api/bootstrap");
    state.key=data.key;
    state.canEdit=!!data.can_edit;
    if (!state.canEdit) {
      document.body.classList.add("read-only");
      const signIn=document.createElement("a");
      signIn.href="/login";
      signIn.textContent="Read-only · Sign in to edit";
      signIn.style.cssText="font:600 12px system-ui;color:inherit;text-decoration:underline;margin-right:12px";
      $("#save-status").before(signIn);
    } else if (!state.key) {
      const signOut=document.createElement("form");
      signOut.method="POST";
      signOut.action="/api/logout";
      signOut.innerHTML='<button type="submit" style="font:600 12px system-ui;border:0;background:none;color:inherit;text-decoration:underline;cursor:pointer">Sign out</button>';
      $("#save-status").before(signOut);
    }
    Object.assign(state,{key:data.key,canEdit:!!data.can_edit,boards:data.boards,folders:data.folders||[],assets:data.assets,fonts:data.fonts||[],items:data.items,trash:data.trash||[]});
    applyCapabilities(data);
    const route=new URLSearchParams(location.search),routeBoard=route.get("board"),routeItem=route.get("item");
    if(state.boards.some(board=>board.id===routeBoard))state.boardId=routeBoard;
    if(state.items.some(item=>item.id===routeItem&&item.board_id===state.boardId))state.selected=new Set([routeItem]);
    loadFonts();renderAll();
    setInterval(async()=>{
      if(state.drag||document.querySelector("dialog[open]"))return;
      try {
        const data=await request("/api/bootstrap");applyCapabilities(data);
        const analysisChanged=data.assets.some((a,i)=>a.auto_analyzed_at!==state.assets[i]?.auto_analyzed_at);
        const fontsChanged=(data.fonts||[]).some((font,i)=>font.id!==state.fonts[i]?.id)||(data.fonts||[]).length!==state.fonts.length;
        if(data.items.length!==state.items.length||data.assets.length!==state.assets.length||analysisChanged||fontsChanged||(data.trash||[]).length!==state.trash.length||data.boards.length!==state.boards.length||(data.folders||[]).length!==state.folders.length){
          const newImage=data.assets.length>state.assets.length;
          Object.assign(state,{items:data.items,assets:data.assets,fonts:data.fonts||[],trash:data.trash||[],boards:data.boards,folders:data.folders||[]});
          if(!state.boards.some(b=>b.id===state.boardId)){state.boardId="main";state.selected.clear();}
          loadFonts();renderAll();if(newImage)toast("New image saved to Library");
        }
      } catch {}
    },4000);
  } catch(e) { toast(`Could not load board: ${e.message}`); }
}
init();

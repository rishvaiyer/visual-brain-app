const $ = selector => document.querySelector(selector);
const token = location.pathname.split("/")[2] || "";
const api = `/api/critique/shared/${encodeURIComponent(token)}`;
let snapshot = null;
let point = null;
function escapeHtml(value) {return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));}
function message(text) {$("#review-message").textContent = text;}
async function fetchJson(path, options) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
function itemCard(item) {
  const image = snapshot.images.find(entry => entry.id === item.asset_id);
  const model = snapshot.models.find(entry => entry.id === item.state.model_id);
  let display = "";
  if (item.kind === "image" && image) display = `<img src="${api}/assets/${encodeURIComponent(image.id)}" alt="${escapeHtml(image.alt || image.title || "Board image")}">`;
  else if (item.kind === "model" && model?.status === "ready") display = `<model-viewer src="${api}/models/${encodeURIComponent(model.id)}" camera-controls alt="Rotatable 3D object"></model-viewer>`;
  else display = '<span class="review-placeholder">Object unavailable</span>';
  const pins = snapshot.comments.filter(note => note.item_id === item.id).map((note, index) =>
    `<button class="review-pin ${note.resolved_at ? "resolved" : ""}" type="button" style="left:${note.x * 100}%;top:${note.y * 100}%" data-pin="${escapeHtml(note.id)}" title="${escapeHtml(note.body)}">${index + 1}</button>`).join("");
  return `<article class="review-object"><div class="review-target" data-item="${escapeHtml(item.id)}">${display}${pins}</div><strong>${escapeHtml(image?.title || (item.kind === "model" ? "3D object" : "Image"))}</strong></article>`;
}
function render() {
  $("#review-title").textContent = snapshot.board.name;
  const items = snapshot.items.filter(item => item.kind === "image" || item.kind === "model")
    .sort((a,b) => a.state.y - b.state.y || a.state.x - b.state.x);
  $("#review-objects").innerHTML = items.length ? items.map(itemCard).join("") : '<p>This board has no images or models to review.</p>';
  if (items.some(item => item.kind === "model")) import("/vendor/model_viewer/model-viewer.min.js").catch(() => message("3D models could not load in this browser."));
  $("#review-objects").querySelectorAll("[data-item]").forEach(target => target.addEventListener("click", event => {
    const pin = event.target.closest("[data-pin]");
    if (pin) {$("#review-notes").querySelector(`[data-note="${pin.dataset.pin}"]`)?.scrollIntoView({block:"nearest"});return;}
    const rect = target.getBoundingClientRect();
    point = {item_id: target.dataset.item, x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))};
    $("#review-point").textContent = "Point placed. Write your note.";
    $("#review-post").disabled = false;
    $("#review-body").focus();
  }));
  $("#review-notes").innerHTML = snapshot.comments.length ? snapshot.comments.map(note =>
    `<article data-note="${escapeHtml(note.id)}" class="review-note ${note.resolved_at ? "resolved" : ""}"><strong>${escapeHtml(note.author)}</strong><time>${escapeHtml(note.created_at.slice(0,10))}</time><p>${escapeHtml(note.body)}</p>${note.resolved_at ? "<small>Resolved</small>" : ""}</article>`).join("") : '<p>No notes yet.</p>';
}
async function load() {
  try {snapshot = await fetchJson(api);render();message("");}
  catch (error) {$("#review-objects").innerHTML = `<p>${escapeHtml(error.message)}</p>`;message(error.message);}
}
$("#review-refresh").addEventListener("click", load);
$("#review-post").addEventListener("click", async () => {
  const body = $("#review-body").value.trim();
  const author = $("#review-author").value.trim();
  if (!point || !body) return message("Choose a point and write a note.");
  $("#review-post").disabled = true;
  try {
    await fetchJson(`${api}/comments`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...point,author:author||"Guest",body})});
    $("#review-body").value = "";
    $("#review-point").textContent = "Choose another spot to add a note.";
    point = null;
    await load();
    message("Note added.");
  } catch(error) {message(error.message);$("#review-post").disabled = false;}
});
load();

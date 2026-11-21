/* Free, local visual discovery. The camera frame is never added to the Library. */
const featureEl = selector => document.querySelector(selector);
const tasteDialog = featureEl("#taste-dialog");
const pointDialog = featureEl("#point-dialog");
let pointStream = null;
let pointCameraRevision = 0;
let pointMatchRevision = 0;
let pointMatchController = null;
let pointMatching = false;
let pointCompanionRevision = 0;
let tasteGroups = [];

function boardForAsset(assetId) {
  const placements = state.items.filter(entry => entry.asset_id === assetId);
  return [...new Set(placements.map(entry => entry.board_id))].map(id => state.boards.find(board => board.id === id)).filter(Boolean);
}

function openBoardFromFeature(boardId) {
  if (!state.boards.some(board => board.id === boardId)) return;
  state.boardId = boardId;
  state.selected.clear();
  tasteDialog.close();
  pointDialog.close();
  featureEl("#sidebar").classList.remove("open");
  renderAll();
  fitContent();
}

function tasteCard(group, index) {
  const examples = group.ids.slice(0, 4).map(id => `<img src="/api/assets/${encodeURIComponent(id)}" alt="" loading="lazy">`).join("");
  return `<button class="taste-group" type="button" data-group="${index}" style="--group-color:${esc(group.color)}"><span class="taste-samples">${examples}</span><span class="taste-group-label"><strong>${esc(group.title)}</strong><small>${group.count} image${group.count === 1 ? "" : "s"}</small></span></button>`;
}

function showTasteGroup(index) {
  const group = tasteGroups[index];
  if (!group) return;
  featureEl("#taste-map-content").querySelectorAll(".taste-group").forEach((button, number) => button.setAttribute("aria-pressed", String(number === index)));
  const cards = group.ids.map(id => {
    const image = asset(id);
    if (!image) return "";
    const boards = boardForAsset(id);
    return `<div class="taste-image"><img src="/api/assets/${encodeURIComponent(id)}" alt="${esc(assetDisplayLabel(image))}" loading="lazy"><div><strong>${esc(assetDisplayLabel(image))}</strong>${boards.length ? `<button type="button" data-open-board="${esc(boards[0].id)}">Open ${esc(boards[0].name)}</button>` : "<small>In Library</small>"}</div></div>`;
  }).join("");
  featureEl("#taste-detail").innerHTML = `<h3>${esc(group.title)}</h3><p>${group.count} saved image${group.count === 1 ? "" : "s"} with related colors, shapes, or tags.</p><div class="taste-image-list">${cards}</div>`;
  featureEl("#taste-detail").querySelectorAll("[data-open-board]").forEach(button => button.addEventListener("click", () => openBoardFromFeature(button.dataset.openBoard)));
}

async function openTasteMap() {
  tasteDialog.showModal();
  featureEl("#taste-map-content").innerHTML = '<p class="feature-loading">Reading your saved images…</p>';
  try {
    const response = await request("/api/taste-map");
    tasteGroups = response.groups || [];
    if (!tasteGroups.length) {
      featureEl("#taste-map-content").innerHTML = '<p class="feature-empty">Save a few images to see their visual threads here.</p>';
      return;
    }
    featureEl("#taste-map-content").innerHTML = `<div class="taste-groups" role="group" aria-label="Visual groups">${tasteGroups.map(tasteCard).join("")}</div><section id="taste-detail" class="taste-detail" aria-live="polite"></section>`;
    featureEl("#taste-map-content").querySelectorAll(".taste-group").forEach(button => button.addEventListener("click", () => showTasteGroup(Number(button.dataset.group))));
    showTasteGroup(0);
  } catch (error) {
    featureEl("#taste-map-content").innerHTML = `<p class="feature-error">${esc(error.message)}</p>`;
  }
}

function stopPointCamera() {
  pointCameraRevision += 1;
  pointStream?.getTracks().forEach(track => track.stop());
  pointStream = null;
  const video = featureEl("#point-video");
  video.srcObject = null;
  video.hidden = true;
  featureEl("#point-camera-off").hidden = false;
  featureEl("#point-capture").disabled = true;
  featureEl("#point-stop").disabled = true;
  featureEl("#point-start").disabled = pointMatching;
  featureEl("#point-start").textContent = "Start camera";
}

async function startPointCamera() {
  if (pointMatching) return;
  const status = featureEl("#point-status");
  if (!navigator.mediaDevices?.getUserMedia) {
    status.textContent = "This browser cannot open a camera here. Choose a photo instead.";
    return;
  }
  stopPointCamera();
  const revision = pointCameraRevision;
  status.textContent = "Opening this device’s camera…";
  featureEl("#point-start").disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: "environment"}}, audio: false});
    if (revision !== pointCameraRevision || !pointDialog.open) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    pointStream = stream;
    const video = featureEl("#point-video");
    video.srcObject = pointStream;
    await video.play();
    if (revision !== pointCameraRevision || !pointDialog.open) return;
    video.hidden = false;
    featureEl("#point-camera-off").hidden = true;
    featureEl("#point-capture").disabled = pointMatching;
    featureEl("#point-stop").disabled = false;
    featureEl("#point-start").disabled = pointMatching;
    featureEl("#point-start").textContent = "Restart camera";
    status.textContent = "This device’s camera is on. Choose Find matching boards when ready.";
    stream.getVideoTracks().forEach(track => track.addEventListener("ended", () => {
      if (pointStream !== stream) return;
      stopPointCamera();
      status.textContent = "Camera off. Start it again or choose a photo.";
    }));
  } catch {
    if (revision !== pointCameraRevision) return;
    stopPointCamera();
    status.textContent = "Camera unavailable or permission was declined. Choose a photo instead.";
  }
}

async function openPointReopen() {
  pointDialog.showModal();
  featureEl("#point-status").textContent = "Camera off. Start this device’s camera or choose a photo.";
  const revision = ++pointCompanionRevision;
  const link = featureEl("#point-companion-open");
  const status = featureEl("#point-companion-status");
  link.hidden = true;
  link.removeAttribute("href");
  if (state.capabilities.demo) {
    status.textContent = "This is a shared playground. The iPhone companion connects to your private Visual Brain library.";
    return;
  }
  if (!state.canEdit) {
    status.textContent = "Sign in to a private Visual Brain library to connect the companion.";
    return;
  }
  status.textContent = "Checking this library’s companion connection…";
  try {
    const details = await request("/api/glasses/status");
    if (revision !== pointCompanionRevision || !pointDialog.open) return;
    if (!details.private_library || !state.canEdit) {
      status.textContent = "Sign in to a private Visual Brain library to connect the companion.";
    } else if (location.protocol !== "https:") {
      status.textContent = "Open your private library at its HTTPS address to connect an iPhone.";
    } else if (details.transport === "iphone-meta-dat" && details.companion_scheme === "visualbrain") {
      const handoff = new URL("visualbrain://connect");
      handoff.searchParams.set("server", location.origin);
      link.href = handoff.href;
      link.hidden = false;
      status.textContent = details.voice_available
        ? "Private library ready. The companion can show and speak your results."
        : "Private library ready. Results appear in the iPhone companion.";
    } else {
      status.textContent = "The companion connection is unavailable on this server. Check the setup guide.";
    }
  } catch {
    if (revision !== pointCompanionRevision || !pointDialog.open) return;
    status.textContent = "The companion connection could not be checked. Reopen this panel to try again, or read the setup guide.";
  }
}

function showPointMatches(matches) {
  if (!matches.length) {
    featureEl("#point-results").innerHTML = '<p class="feature-empty">No saved images were found to compare. Add a reference to your library, then try again.</p>';
    return;
  }
  const cards = matches.slice(0, 8).map(match => {
    const image = asset(match.id);
    const title = typeof match.title === "string" && match.title ? match.title : image ? assetDisplayLabel(image) : "Saved image";
    const boards = (match.boards || []).map((id, index) => {
      const detail = (match.board_names || []).find(board => board?.id === id);
      const suppliedName = detail?.name ?? match.board_names?.[index];
      return {id, name: state.boards.find(board => board.id === id)?.name ||
        (typeof suppliedName === "string" ? suppliedName : "Open board")};
    });
    return `<article class="point-match"><img src="/api/assets/${encodeURIComponent(match.id)}" alt="${esc(title)}"><div><strong>${esc(title)}</strong><small>Similar colors and shapes</small><div class="point-board-links">${boards.length ? boards.map(board => `<a href="/board?board=${encodeURIComponent(board.id)}" data-open-board="${esc(board.id)}">${esc(board.name)}</a>`).join("") : "<span>In Library only</span>"}</div></div></article>`;
  }).join("");
  featureEl("#point-results").innerHTML = `<h3>Similar saved images</h3><p>Compare colors and shapes with your reference before choosing a board.</p>${cards}`;
  featureEl("#point-results").querySelectorAll("[data-open-board]").forEach(link => link.addEventListener("click", event => {
    if (!state.boards.some(board => board.id === link.dataset.openBoard)) return;
    event.preventDefault();
    openBoardFromFeature(link.dataset.openBoard);
  }));
}

async function pointMatch(dataUrl) {
  if (pointMatching || !pointDialog.open) return;
  const revision = ++pointMatchRevision;
  pointMatching = true;
  pointMatchController = new AbortController();
  const status = featureEl("#point-status");
  const results = featureEl("#point-results");
  featureEl("#point-capture").disabled = true;
  featureEl("#point-start").disabled = true;
  featureEl("#point-file-input").disabled = true;
  featureEl("#point-choose").disabled = true;
  status.textContent = "Comparing with your saved images…";
  results.setAttribute("aria-busy", "true");
  results.innerHTML = '<p class="feature-loading">Looking through your saved references…</p>';
  try {
    if (!state.canEdit) throw new Error("Sign in to search this private library.");
    const response = await fetch("/api/camera-match", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-Board-Key": state.key},
      body: JSON.stringify({image_data_url: dataUrl}),
      signal: pointMatchController.signal
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The image could not be compared. Try again.");
    if (revision !== pointMatchRevision || !pointDialog.open) return;
    showPointMatches(result.matches || []);
    status.textContent = "Comparison finished. Your search image was not saved.";
  } catch (error) {
    if (revision !== pointMatchRevision || !pointDialog.open) return;
    status.textContent = error.message;
    results.innerHTML = '<p class="feature-error">The comparison could not finish. Try again or choose another image.</p>';
  } finally {
    if (revision === pointMatchRevision) {
      pointMatching = false;
      pointMatchController = null;
      featureEl("#point-capture").disabled = !pointStream;
      featureEl("#point-start").disabled = false;
      featureEl("#point-file-input").disabled = false;
      featureEl("#point-choose").disabled = false;
      results.setAttribute("aria-busy", "false");
    }
  }
}

featureEl("#open-taste-map").addEventListener("click", openTasteMap);
featureEl("#close-taste-map").addEventListener("click", () => tasteDialog.close());
featureEl("#open-point-reopen").addEventListener("click", openPointReopen);
featureEl("#close-point-reopen").addEventListener("click", () => pointDialog.close());
pointDialog.addEventListener("close", () => {
  pointCompanionRevision += 1;
  pointMatchRevision += 1;
  pointMatchController?.abort();
  pointMatchController = null;
  if (pointMatching) featureEl("#point-results").innerHTML = '<p class="feature-empty">Comparison stopped. Choose an image to try again.</p>';
  pointMatching = false;
  featureEl("#point-file-input").disabled = false;
  featureEl("#point-choose").disabled = false;
  featureEl("#point-results").setAttribute("aria-busy", "false");
  stopPointCamera();
});
window.addEventListener("pagehide", stopPointCamera);
featureEl("#point-start").addEventListener("click", startPointCamera);
featureEl("#point-stop").addEventListener("click", () => {
  stopPointCamera();
  featureEl("#point-status").textContent = "Camera off. Start it again or choose a photo.";
});
featureEl("#point-companion-open").addEventListener("click", stopPointCamera);
featureEl("#point-choose").addEventListener("click", () => featureEl("#point-file-input").click());
featureEl("#point-capture").addEventListener("click", () => {
  const video = featureEl("#point-video");
  if (!video.videoWidth) return;
  const canvas = document.createElement("canvas");
  const scale = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight));
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  pointMatch(canvas.toDataURL("image/jpeg", .82));
});
featureEl("#point-file-input").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) {
    featureEl("#point-status").textContent = "Choose a photo under 8 MB.";
    return;
  }
  try { await pointMatch(await readFile(file)); }
  catch (error) { featureEl("#point-status").textContent = error.message; }
});

/* Free 3D boards, imported models, and at-scale room placement. */
const modelDialog = featureEl("#model-dialog");
let detailModelId = "";
let detailModelBaseHeight = 0;
let detailScaleRevision = 0;

window.create3DBoard = async (itemIds, mode = "separate") => {
  if (!itemIds.length) return toast("Select one or more images first.");
  const grouped = mode === "one-object";
  const limit = Number(grouped ? state.capabilities.max_3d_multi_images : state.capabilities.max_3d_images) || 8;
  const minimum = Number(state.capabilities.min_3d_multi_images) || 32;
  if (grouped && itemIds.length < minimum) return toast(`Choose at least ${minimum} overlapping angles of one still object.`);
  if (itemIds.length > limit) return toast(`Choose up to ${limit} images for one 3D board.`);
  if (!(grouped ? state.capabilities.free_3d_multi : state.capabilities.free_3d)) return toast("3D creation is temporarily unavailable. You can still add a 3D model file.");
  try {
    const result = await request("/api/3d/boards", "POST", {board_id: state.boardId, item_ids: itemIds, mode});
    state.boardId = result.board_id;
    state.selected.clear();
    await refreshData();
    fitContent();
    const quick = !grouped && state.capabilities.free_3d_engine === "builtin";
    toast(grouped ? "One-object board created. Open it later to check the model." : quick ? "New 3D board created. Models appear in a few seconds." : "New 3D board created. Open it later to check the models.");
    // The built-in engine finishes in seconds, so show the result without a manual check.
    if (quick) for (const delay of [2500, 7000]) setTimeout(() => { if (state.boardId === result.board_id && state.models.some(entry => ["queued", "running"].includes(entry.status))) refreshData().catch(() => {}); }, delay);
  } catch (error) { toast(error.message); }
};

featureEl("#import-model").addEventListener("click", () => featureEl("#model-file-input").click());
featureEl("#model-file-input").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".glb") || file.size > 20 * 1024 * 1024) return toast("Choose a GLB model under 20 MB.");
  try {
    const result = await request("/api/models", "POST", {model_data_url: await readFile(file), board_id: state.boardId});
    state.models.unshift(result.model);
    state.items.push(result.item);
    state.selected = new Set([result.item.id]);
    renderAll();
    toast("3D model added to this board.");
  } catch (error) { toast(error.message); }
});

window.openModelDetail = async modelId => {
  const entry = model(modelId);
  if (!entry || entry.status !== "ready") return toast("This model is not ready yet.");
  detailModelId = modelId;
  const source = asset(entry.source_asset_id);
  const sourceCount = Array.isArray(entry.source_asset_ids) ? entry.source_asset_ids.length : 0;
  featureEl("#model-dialog-source").textContent = sourceCount > 1 ? `Made from ${sourceCount} angles. Rotate it or place it in your room.` : source ? `From ${assetDisplayLabel(source)}. Rotate it or place it in your room.` : "Rotate it or place it in your room.";
  if (String(entry.engine || "").startsWith("builtin")) featureEl("#model-dialog-source").textContent += " Quick 3D: the front follows the photo, the back is a guess.";
  const sourceItem = state.items.find(entryItem => entryItem.state.model_id === modelId);
  featureEl("#model-height").value = sourceItem?.state.real_height_cm || "";
  featureEl("#model-placement").value = sourceItem?.state.ar_placement || "floor";
  modelDialog.showModal();
  try {
    await ensureModelViewer();
    const oldViewer = featureEl("#model-detail-viewer");
    const viewer = oldViewer.cloneNode(false);
    viewer.removeAttribute("src");
    oldViewer.replaceWith(viewer);
    detailModelBaseHeight = 0;
    viewer.scale = "1 1 1";
    viewer.addEventListener("load", () => {
      if (detailModelId !== modelId) return;
      detailModelBaseHeight = viewer.getDimensions()?.y || 0;
      updateRoomPlacement();
    }, {once: true});
    viewer.src = `/api/models/${encodeURIComponent(modelId)}`;
    updateRoomPlacement();
  } catch { featureEl("#model-ar-note").textContent = "3D viewer could not load on this browser."; }
};

function updateRoomPlacement() {
  const viewer = featureEl("#model-detail-viewer");
  const height = Number(featureEl("#model-height").value);
  const note = featureEl("#model-ar-note");
  const button = featureEl("#place-model");
  if (!(height >= 1 && height <= 500) || !detailModelBaseHeight) {
    button.disabled = true;
    note.textContent = "Enter the real object height first. Placement needs a compatible phone.";
    return;
  }
  const scale = height / 100 / detailModelBaseHeight;
  viewer.scale = `${scale} ${scale} ${scale}`;
  const revision = ++detailScaleRevision;
  const openModel = detailModelId;
  viewer.updateComplete.then(() => {
    if (revision !== detailScaleRevision || detailModelId !== openModel) return;
    return viewer.updateFraming();
  }).then(() => {
    if (revision === detailScaleRevision && detailModelId === openModel) viewer.jumpCameraToGoal();
  }).catch(() => {});
  viewer.setAttribute("ar-placement", featureEl("#model-placement").value);
  button.disabled = !viewer.canActivateAR;
  note.textContent = viewer.canActivateAR ? "Ready to place at the height you entered." : "This browser cannot open a live room view. Try this model on a compatible phone.";
  const sourceItem = state.items.find(entry => entry.state.model_id === detailModelId);
  if (sourceItem && state.canEdit) {
    sourceItem.state.real_height_cm = height;
    sourceItem.state.ar_placement = featureEl("#model-placement").value;
    persistItem(sourceItem);
  }
}

featureEl("#model-height").addEventListener("change", updateRoomPlacement);
featureEl("#model-placement").addEventListener("change", updateRoomPlacement);
featureEl("#place-model").addEventListener("click", async () => {
  const viewer = featureEl("#model-detail-viewer");
  try { await viewer.activateAR(); }
  catch { featureEl("#model-ar-note").textContent = "Room placement did not start on this device."; }
});
featureEl("#snapshot-model").addEventListener("click", () => {
  const viewer = featureEl("#model-detail-viewer");
  if (!detailModelId || !viewer.modelIsVisible) return toast("Wait for the 3D object to load first.");
  try {
    const link = document.createElement("a");
    link.href = viewer.toDataURL("image/png");
    link.download = `${detailModelId}-view.png`;
    link.click();
  } catch { toast("Could not save this view on this browser."); }
});
featureEl("#save-model").addEventListener("click", () => {
  if (!detailModelId) return;
  const link = document.createElement("a");
  link.href = `/api/models/${encodeURIComponent(detailModelId)}`;
  link.download = `${detailModelId}.glb`;
  link.click();
});
featureEl("#close-model-dialog").addEventListener("click", () => modelDialog.close());
modelDialog.addEventListener("close", () => {featureEl("#model-detail-viewer").src = "";detailModelId = "";detailModelBaseHeight = 0;detailScaleRevision++;});

/* A spatial walk through the current board; no scene is uploaded elsewhere. */
const walkDialog = featureEl("#walk-dialog");
let walkItems = [];
let walkIndex = 0;
function walkPanel(entry, index) {
  const side = index % 2 ? "right" : "left";
  const x = side === "left" ? "var(--walk-left-x)" : "var(--walk-right-x)";
  const z = `calc(var(--walk-start-z) - ${index * 430}px)`;
  const turn = side === "left" ? 12 : -12;
  let body = "";
  if (entry.kind === "image") {
    const image = asset(entry.asset_id);
    body = `<img src="/api/assets/${encodeURIComponent(entry.asset_id)}" alt="${esc(assetDisplayLabel(image))}" loading="lazy"><strong>${esc(assetDisplayLabel(image))}</strong>`;
  } else if (entry.kind === "model") {
    const modelEntry = model(entry.state.model_id);
    const source = modelEntry?.source_asset_id;
    body = `${source ? `<img src="/api/assets/${encodeURIComponent(source)}" alt="" loading="lazy">` : '<div class="walk-model-symbol">◫</div>'}<strong>3D object</strong>${modelEntry?.status === "ready" ? `<button type="button" data-walk-model="${esc(modelEntry.id)}">Open 3D</button>` : '<small>Model is being made</small>'}`;
  } else {
    body = `<div class="walk-text">${esc(entry.state.text || "Text")}</div>`;
  }
  return `<article class="walk-panel ${side}" style="transform:translate3d(${x},-155px,${z}) rotateY(${turn}deg)"><div class="walk-art">${body}</div><button type="button" data-walk-item="${esc(entry.id)}">Open on board</button></article>`;
}
function renderWalk() {
  const scene = featureEl("#walk-scene");
  scene.style.transform = `translateZ(${walkIndex * 430}px)`;
  featureEl("#walk-count").textContent = walkItems.length ? `${walkIndex + 1} of ${walkItems.length}` : "Empty board";
  featureEl("#walk-back").disabled = walkIndex === 0;
  featureEl("#walk-next").disabled = walkIndex >= walkItems.length - 1;
}
function moveWalk(step) {walkIndex = Math.max(0, Math.min(walkItems.length - 1, walkIndex + step));renderWalk();}
featureEl("#open-walk-board").addEventListener("click", () => {
  walkItems = currentItems().filter(entry => ["image", "model", "text", "frame"].includes(entry.kind))
    .sort((left, right) => Number(!["image", "model"].includes(left.kind)) - Number(!["image", "model"].includes(right.kind))
      || left.state.y - right.state.y || left.state.x - right.state.x);
  walkIndex = 0;
  featureEl("#walk-title").textContent = `Walk · ${currentBoard().name}`;
  featureEl("#walk-empty").hidden = walkItems.length > 0;
  featureEl("#walk-scene").innerHTML = walkItems.map(walkPanel).join("");
  featureEl("#walk-scene").querySelectorAll("[data-walk-item]").forEach(button => button.addEventListener("click", () => {
    walkDialog.close();state.selected = new Set([button.dataset.walkItem]);renderAll();
  }));
  featureEl("#walk-scene").querySelectorAll("[data-walk-model]").forEach(button => button.addEventListener("click", () => {
    walkDialog.close();window.openModelDetail?.(button.dataset.walkModel);
  }));
  renderWalk();walkDialog.showModal();featureEl("#walk-view").focus();
});
featureEl("#close-walk").addEventListener("click", () => walkDialog.close());
featureEl("#walk-back").addEventListener("click", () => moveWalk(-1));
featureEl("#walk-next").addEventListener("click", () => moveWalk(1));
walkDialog.addEventListener("keydown", event => {
  if (["ArrowRight", "ArrowDown", "w", "W"].includes(event.key)) {event.preventDefault();moveWalk(1);}
  if (["ArrowLeft", "ArrowUp", "s", "S"].includes(event.key)) {event.preventDefault();moveWalk(-1);}
});

/* Board-scoped critique. A private link gives read/comment access to this board only. */
const critiqueDialog = featureEl("#critique-dialog");
let critiqueData = null;
let critiqueBoardId = "";
let critiquePoint = null;
function critiqueObject(entry) {
  const image = entry.kind === "image" ? asset(entry.asset_id) : null;
  const modelEntry = entry.kind === "model" ? model(entry.state.model_id) : null;
  const source = modelEntry?.source_asset_id;
  const content = image ? `<img src="/api/assets/${encodeURIComponent(image.id)}" alt="${esc(assetDisplayLabel(image))}">`
    : modelEntry?.status === "ready" ? `<model-viewer src="/api/models/${encodeURIComponent(modelEntry.id)}" camera-controls alt="3D object"></model-viewer>`
      : source ? `<img src="/api/assets/${encodeURIComponent(source)}" alt="Source image">` : '<span class="critique-model-placeholder">3D object</span>';
  const pins = critiqueData.comments.filter(note => note.item_id === entry.id).map((note, index) =>
    `<button type="button" class="critique-pin ${note.resolved_at ? "resolved" : ""}" style="left:${note.x * 100}%;top:${note.y * 100}%" data-note="${esc(note.id)}" title="${esc(note.body)}">${index + 1}</button>`).join("");
  return `<article class="critique-object"><div class="critique-target" data-critique-item="${esc(entry.id)}">${content}${pins}</div><strong>${esc(image ? assetDisplayLabel(image) : "3D object")}</strong></article>`;
}
function renderCritique() {
  const entries = critiqueData.items.filter(entry => ["image", "model"].includes(entry.kind));
  featureEl("#critique-objects").innerHTML = entries.length ? entries.map(critiqueObject).join("") : '<p class="feature-empty">Add an image or 3D object to this board first.</p>';
  if (entries.some(entry => entry.kind === "model" && model(entry.state.model_id)?.status === "ready")) ensureModelViewer();
  featureEl("#critique-objects").querySelectorAll("[data-critique-item]").forEach(target => target.addEventListener("click", event => {
    const pin = event.target.closest("[data-note]");
    if (pin) {featureEl("#critique-notes").querySelector(`[data-note-card="${pin.dataset.note}"]`)?.scrollIntoView({block:"nearest"});return;}
    const rect = target.getBoundingClientRect();
    critiquePoint = {item_id: target.dataset.critiqueItem,
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))};
    featureEl("#critique-new>p").textContent = "Point placed. Write what should change.";
    featureEl("#critique-post").disabled = false;
    if (window.innerWidth <= 700) featureEl("#critique-new").scrollIntoView({block:"nearest"});
    featureEl("#critique-body").focus();
  }));
  featureEl("#critique-notes").innerHTML = critiqueData.comments.length ? critiqueData.comments.map(note =>
    `<article class="critique-note ${note.resolved_at ? "resolved" : ""}" data-note-card="${esc(note.id)}"><strong>${esc(note.author)}</strong><time>${esc(note.created_at.slice(0,10))}</time><p>${esc(note.body)}</p>${note.resolved_at ? '<small>Resolved</small>' : `<button type="button" data-resolve-note="${esc(note.id)}">Resolve</button>`}</article>`).join("") : '<p class="muted">No notes yet.</p>';
  featureEl("#critique-notes").querySelectorAll("[data-resolve-note]").forEach(button => button.addEventListener("click", async () => {
    try {await request(`/api/critique/resolve/${button.dataset.resolveNote}`, "POST", {});await loadCritique();}
    catch(error){toast(error.message);}
  }));
  const shareAllowed = !state.capabilities.demo;
  featureEl("#critique-create-link").disabled = !shareAllowed;
  featureEl("#critique-share-status").textContent = shareAllowed ? (critiqueData.invite_active ? "A private link is active. Create a new one to replace it." : "No private link is active.") : "The public playground is visible to everyone. Private links work in your own board.";
  featureEl("#critique-revoke-link").hidden = !critiqueData.invite_active;
}
async function loadCritique() {
  critiqueData = await request(`/api/critique/board/${encodeURIComponent(critiqueBoardId)}`);
  renderCritique();
}
featureEl("#open-critique").addEventListener("click", async () => {
  if (!state.canEdit) return toast("Sign in to critique this board.");
  critiqueBoardId = state.boardId;
  featureEl("#critique-title").textContent = `Critique · ${currentBoard().name}`;
  critiquePoint = null;
  featureEl("#critique-post").disabled = true;
  featureEl("#critique-link-row").hidden = true;
  critiqueDialog.showModal();
  try {await loadCritique();} catch(error){featureEl("#critique-objects").innerHTML = `<p class="feature-error">${esc(error.message)}</p>`;}
});
featureEl("#close-critique").addEventListener("click", () => critiqueDialog.close());
featureEl("#critique-post").addEventListener("click", async () => {
  if (!critiquePoint || !featureEl("#critique-body").value.trim()) return toast("Write a note first.");
  try {
    await request("/api/critique/comments", "POST", {...critiquePoint,board_id:critiqueBoardId,
      author:featureEl("#critique-author").value.trim() || "You",body:featureEl("#critique-body").value.trim()});
    featureEl("#critique-body").value = "";
    critiquePoint = null;
    featureEl("#critique-post").disabled = true;
    featureEl("#critique-new>p").textContent = "Click an image or 3D object to place another note.";
    await loadCritique();
  } catch(error){toast(error.message);}
});
featureEl("#critique-create-link").addEventListener("click", async () => {
  try {
    const result = await request("/api/critique/invite", "POST", {board_id:critiqueBoardId});
    featureEl("#critique-link").value = new URL(result.path, location.origin).href;
    featureEl("#critique-link-row").hidden = false;
    critiqueData.invite_active = true;
    featureEl("#critique-revoke-link").hidden = false;
    featureEl("#critique-share-status").textContent = "Only this board is shared. Copy the link to invite a reviewer.";
  } catch(error){featureEl("#critique-share-status").textContent = error.message;}
});
featureEl("#critique-copy-link").addEventListener("click", async () => {
  try {await navigator.clipboard.writeText(featureEl("#critique-link").value);featureEl("#critique-share-status").textContent = "Private link copied.";}
  catch {featureEl("#critique-link").select();featureEl("#critique-share-status").textContent = "Select and copy this private link.";}
});
featureEl("#critique-revoke-link").addEventListener("click", async () => {
  try {
    await request(`/api/critique/invite/${encodeURIComponent(critiqueBoardId)}`, "DELETE");
    featureEl("#critique-link-row").hidden = true;
    featureEl("#critique-link").value = "";
    critiqueData.invite_active = false;
    featureEl("#critique-revoke-link").hidden = true;
    featureEl("#critique-share-status").textContent = "Link revoked. Reviewers can no longer open it.";
  } catch(error){featureEl("#critique-share-status").textContent = error.message;}
});

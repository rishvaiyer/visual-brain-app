const MENU_ID = "visual-brain-save-image";
const MENU_DEFAULT = "visual-brain-default";
const MENU_BOARD_PREFIX = "visual-brain-board:";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:8777";
const MAX_IMAGE_BYTES = 16_000_000;
const MAX_JSON_BYTES = 24_000_000;
const MENU_CONTEXTS = ["image", "link", "page"];

let menuRefresh = Promise.resolve();
let menuVisibilitySerial = 0;
const visualBrainImageTabs = new Set();
const activeCaptureFrames = new Map();
function createContextMenu() {
  menuRefresh = menuRefresh.catch(() => {}).then(async () => {
    let boards = [];
    try {
      const settings = await readSettings();
      if (settings.pairingKey) {
        const response = await fetch(new URL("/api/boards-list", validateBackendUrl(settings.backendUrl)).toString(), {headers:{"X-Board-Key":settings.pairingKey}});
        if (response.ok) boards = (await response.json()).boards || [];
      }
    } catch { /* The default save option works even while the local app is closed. */ }
    await chrome.contextMenus.removeAll();
    chrome.contextMenus.create({id:MENU_ID,title:"Save image to Visual Brain",contexts:MENU_CONTEXTS});
    chrome.contextMenus.create({id:MENU_DEFAULT,parentId:MENU_ID,title:"Everything + Library",contexts:MENU_CONTEXTS});
    const choices=boards.filter(board=>board.id !== "main");
    if (choices.length) chrome.contextMenus.create({id:"visual-brain-divider",parentId:MENU_ID,type:"separator",contexts:MENU_CONTEXTS});
    for (const board of choices) chrome.contextMenus.create({id:MENU_BOARD_PREFIX+board.id,parentId:MENU_ID,title:`Save to ${board.name}`,contexts:MENU_CONTEXTS});
    const [activeTab] = await chrome.tabs.query({active:true,lastFocusedWindow:true});
    if (activeTab?.id) await syncSaveMenuVisibility(activeTab.id, activeTab.url);
  });
  return menuRefresh;
}

chrome.runtime.onInstalled.addListener(createContextMenu);
chrome.runtime.onStartup.addListener(createContextMenu);
createContextMenu();

chrome.tabs.onActivated.addListener(({tabId}) => syncSaveMenuVisibility(tabId));
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tab.active && (changeInfo.url || changeInfo.status === "complete")) syncSaveMenuVisibility(tabId, tab.url);
});
chrome.tabs.onRemoved.addListener(tabId => { visualBrainImageTabs.delete(tabId); activeCaptureFrames.delete(tabId); });
chrome.tabs.query({active:true,lastFocusedWindow:true}).then(([tab]) => { if (tab?.id) syncSaveMenuVisibility(tab.id, tab.url); }).catch(() => {});

chrome.commands.onCommand.addListener(async command => {
  if (command !== "save-current-image") return;
  const [tab] = await chrome.tabs.query({active:true,lastFocusedWindow:true});
  if (!tab?.id) return;
  const frameId = activeCaptureFrames.get(tab.id) ?? 0;
  try {
    await chrome.tabs.sendMessage(tab.id, {type:"VB_QUICK_CAPTURE"}, {frameId});
  } catch {
    // Browser-internal pages and tabs without the content script cannot be captured.
  }
});

async function syncSaveMenuVisibility(tabId, knownUrl = "") {
  const serial = ++menuVisibilitySerial;
  try {
    const tab = knownUrl ? {id:tabId,url:knownUrl} : await chrome.tabs.get(tabId);
    const settings = await readSettings();
    const backendOrigin = validateBackendUrl(settings.backendUrl).origin;
    const hidden = isVisualBrainUrl(tab.url || "", backendOrigin) || visualBrainImageTabs.has(tabId);
    if (serial === menuVisibilitySerial) await chrome.contextMenus.update(MENU_ID, {visible:!hidden});
  } catch { /* The context menu may not exist during extension startup. */ }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const menuId = String(info.menuItemId);
  if ((menuId !== MENU_DEFAULT && !menuId.startsWith(MENU_BOARD_PREFIX)) || !tab?.id) return;
  const boardId = menuId.startsWith(MENU_BOARD_PREFIX) ? menuId.slice(MENU_BOARD_PREFIX.length) : null;

  let pageCapture = null;
  try {
    pageCapture = await chrome.tabs.sendMessage(tab.id, { type: "VB_GET_CONTEXT_CAPTURE" }, {frameId: info.frameId ?? 0});
  } catch {
    // The image URL supplied by Chrome remains a useful fallback if the page script is unavailable.
  }

  const capture = pageCapture || {
    source_url: info.pageUrl || tab.url,
    image_url: info.srcUrl || (looksLikeImageUrl(info.linkUrl) ? info.linkUrl : ""),
    image_candidates: [info.srcUrl, looksLikeImageUrl(info.linkUrl) ? info.linkUrl : ""].filter(Boolean),
    alt: "",
    title: ""
  };

  let result;
  try {
    result = await performCapture(capture, info.frameUrl || tab.url, boardId, {tab,frameId:info.frameId ?? 0});
  } catch (error) {
    result = { ok: false, message: userMessage(error) };
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "VB_CAPTURE_RESULT", result }, {frameId: info.frameId ?? 0});
  } catch {
    // The capture itself is complete even if the page was closed before its toast could appear.
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "VB_ACTIVE_CAPTURE_TARGET") {
    const tabId = sender.tab?.id;
    if (tabId !== undefined) {
      if (message.active === true) activeCaptureFrames.set(tabId, sender.frameId ?? 0);
      else if (activeCaptureFrames.get(tabId) === (sender.frameId ?? 0)) activeCaptureFrames.delete(tabId);
    }
    return undefined;
  }
  if (message?.type === "VB_VISUAL_BRAIN_IMAGE_HOVER") {
    const tabId = sender.tab?.id;
    if (tabId !== undefined) {
      if (message.isVisualBrain === true) visualBrainImageTabs.add(tabId);
      else visualBrainImageTabs.delete(tabId);
      if (sender.tab?.active) syncSaveMenuVisibility(tabId, sender.tab.url);
    }
    sendResponse({ok:true});
    return false;
  }
  if (message?.type === "VB_GET_CAPTURE_SETTINGS") {
    readSettings().then(settings => sendResponse({backendOrigin:validateBackendUrl(settings.backendUrl).origin})).catch(() => sendResponse({backendOrigin:DEFAULT_BACKEND_URL}));
    return true;
  }
  if (message?.type === "VB_REFRESH_BOARDS") {
    createContextMenu().then(() => sendResponse({ok:true})).catch(() => sendResponse({ok:false}));
    return true;
  }
  if (message?.type !== "VB_CAPTURE") return undefined;

  (async () => {
    try {
      const pageUrl = sender.url || sender.tab?.url || "";
      if (!sender.tab || !isHttpUrl(pageUrl)) {
        throw new Error("Capture requests must come from a web page.");
      }
      const result = await performCapture(message.capture, pageUrl, null, {tab:sender.tab,frameId:sender.frameId ?? 0});
      sendResponse(result);
    } catch (error) {
      sendResponse({ ok: false, message: userMessage(error) });
    }
  })();

  return true;
});

async function performCapture(input, authoritativePageUrl, boardId = null, context = {}) {
  const settings = await readSettings();
  const backend = validateBackendUrl(settings.backendUrl);
  const pairingKey = String(settings.pairingKey || "").trim();
  if (!pairingKey) throw new Error("Set the pairing key in Visual Brain settings first.");

  const pageUrl = isHttpUrl(authoritativePageUrl)
    ? authoritativePageUrl
    : (isHttpUrl(input?.source_url) ? input.source_url : "");
  if (!pageUrl) throw new Error("The source page URL is not an HTTP or HTTPS address.");
  const backendOrigin = validateBackendUrl(settings.backendUrl).origin;
  if (isVisualBrainUrl(pageUrl, backendOrigin)) throw new Error("This Visual Brain content is already saved here.");

  const candidates = normalizeCandidates(input, pageUrl);
  if (candidates.some(candidate => isVisualBrainUrl(candidate, backendOrigin))) throw new Error("This Visual Brain image is already saved here.");
  if (!candidates.length && !input?.image_data_url && !input?.screen_crop) throw new Error("No image found under this click. Try clicking directly on the visual.");

  const capture = {
    source_url: pageUrl,
    image_url: candidates[0] || "",
    alt: cleanText(input?.alt, 2_000),
    title: cleanText(input?.title || input?.alt || context.tab?.title, 500)
  };
  if (settings.favorite === true) capture.favorite = true;
  if (boardId) capture.board_id = boardId;

  let imageNote = "", clipped = false;
  if (typeof input?.image_data_url === "string" && /^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(input.image_data_url)) capture.image_data_url=input.image_data_url;
  for (const candidate of capture.image_data_url ? [] : candidates) {
    try {
      const fetched = await fetchImageData(candidate);
      capture.image_url = fetched.imageUrl;
      capture.image_data_url = fetched.dataUrl;
      imageNote = "";
      break;
    } catch (error) {
      imageNote = userMessage(error);
    }
  }

  if (!capture.image_data_url && input?.screen_crop && context.frameId === 0) {
    try {
      capture.image_data_url=await captureVisibleClip(context.tab);
      capture.image_url="";
      capture.screen_crop=input.screen_crop;
      clipped=true;
    } catch (error) { imageNote=userMessage(error); }
  }
  if (!capture.image_data_url) {
    const reason = imageNote ? ` ${imageNote}` : "";
    throw new Error(`Could not capture this image.${reason}`);
  }

  const json = JSON.stringify(capture);
  if (new TextEncoder().encode(json).byteLength > MAX_JSON_BYTES) {
    throw new Error("The image is too large to send. Try a smaller image.");
  }

  const response = await fetch(new URL("/api/capture", backend).toString(), {
    method: "POST",
    redirect: "error",
    headers: {
      "Content-Type": "application/json",
      "X-Board-Key": pairingKey
    },
    body: json
  });

  let body = {};
  try {
    body = await response.json();
  } catch {
    // Keep the UI message generic if the backend returns a non-JSON error page.
  }
  if (!response.ok || body?.error) {
    const reason = cleanText(body?.error, 240);
    throw new Error(reason || `Visual Brain returned HTTP ${response.status}.`);
  }

  const savedMessage = clipped ? "Saved visible image clip to Visual Brain" : boardId ? (body?.duplicate ? "Added to chosen board (image already in Library)" : "Saved to chosen board + Library") : (body?.duplicate ? "Added to Everything (image already in Library)" : "Saved to Everything + Library");
  return {
    ok: true,
    duplicate: Boolean(body?.duplicate),
    message: savedMessage
  };
}

function looksLikeImageUrl(value) {
  try { return /\.(?:jpe?g|png|webp|gif|avif)(?:$|[?#])/i.test(new URL(value).pathname); }
  catch { return false; }
}

function isVisualBrainUrl(value, backendOrigin) {
  try {
    const target = new URL(value);
    const backend = new URL(backendOrigin || DEFAULT_BACKEND_URL);
    const isLoopback = host => host === "localhost" || host === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(host);
    return target.protocol === "http:" && target.port === backend.port && isLoopback(target.hostname.toLowerCase()) && isLoopback(backend.hostname.toLowerCase());
  } catch {
    return false;
  }
}

async function captureVisibleClip(tab) {
  if (!tab?.active || !Number.isInteger(tab.windowId)) throw new Error("Keep this tab active while saving the visible image.");
  const [activeTab] = await chrome.tabs.query({active:true,windowId:tab.windowId});
  if (activeTab?.id !== tab.id) throw new Error("The page changed while capturing. Try again on the image tab.");
  try {
    await chrome.tabs.sendMessage(tab.id,{type:"VB_CAPTURE_SCREENSHOT_PREPARE"},{frameId:0});
  } catch { /* A missing content script will be reported by the missing crop. */ }
  try {
    let dataUrl=await chrome.tabs.captureVisibleTab(tab.windowId,{format:"png"});
    if (new TextEncoder().encode(dataUrl).byteLength > MAX_JSON_BYTES) dataUrl=await chrome.tabs.captureVisibleTab(tab.windowId,{format:"jpeg",quality:88});
    if (new TextEncoder().encode(dataUrl).byteLength > MAX_JSON_BYTES) throw new Error("Visible image is too large to capture.");
    return dataUrl;
  } finally {
    chrome.tabs.sendMessage(tab.id,{type:"VB_CAPTURE_SCREENSHOT_RESTORE"},{frameId:0}).catch(()=>{});
  }
}

function normalizeCandidates(input, pageUrl) {
  const rawCandidates = [input?.image_url, ...(Array.isArray(input?.image_candidates) ? input.image_candidates : [])];
  const candidates = [];
  for (const raw of rawCandidates.slice(0, 12)) {
    if (typeof raw !== "string") continue;
    try {
      const url = new URL(raw, pageUrl);
      if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password) continue;
      const cleaned = url.toString();
      if (!candidates.includes(cleaned)) candidates.push(cleaned);
    } catch {
      // Ignore malformed candidates and retain the other URLs extracted from this image.
    }
  }
  return candidates;
}

async function fetchImageData(imageUrl) {
  const response = await fetch(imageUrl, {
    method: "GET",
    mode: "cors",
    credentials: "omit",
    cache: "no-store",
    redirect: "follow",
    headers: { "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8" }
  });
  if (!response.ok) throw new Error(`Image request returned HTTP ${response.status}.`);

  const contentLength = Number(response.headers.get("content-length") || 0);
  if (contentLength > MAX_IMAGE_BYTES) throw new Error("The image exceeds the 16 MB capture limit.");

  const blob = await readBoundedBlob(response);
  const prefix = new Uint8Array(await blob.slice(0, 24).arrayBuffer());
  const mime = usableImageMime(blob.type, prefix);
  if (!mime) throw new Error("The image host did not return recognizable image bytes.");

  const dataUrl = await blobToDataUrl(blob, mime);
  const finalUrl = isHttpUrl(response.url) ? response.url : imageUrl;
  return { dataUrl, imageUrl: finalUrl };
}

async function readBoundedBlob(response) {
  if (!response.body) {
    const blob = await response.blob();
    if (blob.size > MAX_IMAGE_BYTES) throw new Error("The image exceeds the 16 MB capture limit.");
    return blob;
  }

  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_IMAGE_BYTES) {
        await reader.cancel();
        throw new Error("The image exceeds the 16 MB capture limit.");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  return new Blob(chunks, { type: response.headers.get("content-type") || "" });
}

function usableImageMime(headerMime, bytes) {
  const mime = String(headerMime || "").split(";")[0].trim().toLowerCase();
  if (mime.startsWith("image/")) return mime;
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "image/jpeg";
  if (bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return "image/png";
  if (bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46) return "image/gif";
  if (bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50) return "image/webp";
  if (bytes[4] === 0x66 && bytes[5] === 0x74 && bytes[6] === 0x79 && bytes[7] === 0x70) return "image/avif";
  return "";
}

async function blobToDataUrl(blob, mime) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length));
    binary += String.fromCharCode(...chunk);
  }
  return `data:${mime};base64,${btoa(binary)}`;
}

async function readSettings() {
  const stored = await chrome.storage.local.get("settings");
  return {
    backendUrl: stored.settings?.backendUrl || DEFAULT_BACKEND_URL,
    pairingKey: stored.settings?.pairingKey || "",
    favorite: stored.settings?.favorite === true
  };
}

function validateBackendUrl(value) {
  let url;
  try {
    url = new URL(String(value || DEFAULT_BACKEND_URL).trim());
  } catch {
    throw new Error("The backend URL is invalid. Use a local URL such as http://127.0.0.1:8777.");
  }
  const host = url.hostname.toLowerCase();
  const loopback = host === "localhost" || host === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(host);
  if (!loopback || url.protocol !== "http:" || url.username || url.password || url.search || url.hash) {
    throw new Error("The backend must be an HTTP loopback address, such as http://127.0.0.1:8777.");
  }
  return url;
}

function isHttpUrl(value) {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password;
  } catch {
    return false;
  }
}

function cleanText(value, limit) {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim().slice(0, limit);
}

function userMessage(error) {
  const message = error instanceof Error ? error.message : "Capture failed.";
  return cleanText(message, 300) || "Capture failed.";
}

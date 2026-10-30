(() => {
  const HOST_ID = "__visual_brain_capture_ui__";
  const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8777";
  if (isVisualBrainUrl(location.href, DEFAULT_BACKEND_ORIGIN)) return;
  chrome.runtime.sendMessage({type:"VB_GET_CAPTURE_SETTINGS"}).then(settings => {
    const backendOrigin = settings?.backendOrigin || DEFAULT_BACKEND_ORIGIN;
    if (!isVisualBrainUrl(location.href, backendOrigin)) mountCaptureUi(backendOrigin);
  }).catch(() => {
    if (!isVisualBrainUrl(location.href, DEFAULT_BACKEND_ORIGIN)) mountCaptureUi(DEFAULT_BACKEND_ORIGIN);
  });

  function isVisualBrainUrl(value, backendOrigin) {
    try {
      const target = new URL(value);
      const backend = new URL(backendOrigin || DEFAULT_BACKEND_ORIGIN);
      const isLoopback = host => host === "localhost" || host === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(host);
      return target.protocol === "http:" && target.port === backend.port && isLoopback(target.hostname.toLowerCase()) && isLoopback(backend.hostname.toLowerCase());
    } catch {
      return false;
    }
  }

  function mountCaptureUi(backendOrigin) {
    if (document.getElementById(HOST_ID)) return;

  const host = document.createElement("div");
  host.id = HOST_ID;
  host.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:none;";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      :host { all: initial; }
      #save {
        position: fixed; display: none; align-items: center; gap: 7px;
        padding: 9px 13px; border: 1px solid rgba(255,255,255,.2); border-radius: 999px;
        color: #fff; background: #17191f; box-shadow: 0 5px 18px rgba(0,0,0,.34);
        font: 600 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        letter-spacing: .01em; cursor: pointer; pointer-events: auto; white-space: nowrap;
      }
      #save:hover { background: #2b303b; }
      #save:disabled { opacity: .76; cursor: progress; }
      #toast {
        position: fixed; top: 18px; right: 18px; display: none; max-width: min(360px, calc(100vw - 36px));
        padding: 11px 15px; border-radius: 10px; color: #fff; background: #17191f;
        box-shadow: 0 5px 18px rgba(0,0,0,.34); font: 500 13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        pointer-events: none;
      }
    </style>
    <button id="save" type="button" aria-label="Save image to Visual Brain"><span aria-hidden="true">＋</span><span id="label">Save to Visual Brain</span></button>
    <div id="toast" role="status" aria-live="polite"></div>
  `;
  document.documentElement.appendChild(host);

  const button = shadow.getElementById("save");
  const label = shadow.getElementById("label");
  const toast = shadow.getElementById("toast");
  let activeVisual = null;
  let hideTimer = 0;
  let toastTimer = 0;
  let contextCapture = null;
  let visualBrainContext = false;

  function reportActiveCaptureTarget(active) {
    chrome.runtime.sendMessage({type:"VB_ACTIVE_CAPTURE_TARGET",active}).catch(() => {});
  }

  function reportVisualBrainContext(isVisualBrain) {
    if (visualBrainContext === isVisualBrain) return;
    visualBrainContext = isVisualBrain;
    chrome.runtime.sendMessage({type:"VB_VISUAL_BRAIN_IMAGE_HOVER",isVisualBrain}).catch(() => {});
  }

  document.addEventListener("pointerover", (event) => {
    if (event.composedPath().includes(host)) return;
    const visual = VisualBrainCaptureTarget.find(event);
    if (!visual?.element?.isConnected) { reportVisualBrainContext(false); reportActiveCaptureTarget(false); return; }
    const visualUrl = visual.kind === "image" ? (visual.element.currentSrc || visual.element.src) : visual.url;
    const isVisualBrain = isVisualBrainUrl(location.href, backendOrigin) || isVisualBrainUrl(visualUrl, backendOrigin);
    reportVisualBrainContext(isVisualBrain);
    if (isVisualBrain) { hideButton(); return; }
    activeVisual = visual;
    reportActiveCaptureTarget(true);
    window.clearTimeout(hideTimer);
    positionButton(visual.element);
  }, true);

  document.addEventListener("pointerout", (event) => {
    if (event.composedPath().includes(host)) return;
    const next = event.relatedTarget;
    if (next === host || (next instanceof Node && host.contains(next))) return;
    if (!next) reportVisualBrainContext(false);
    if (!activeVisual) return;
    hideTimer = window.setTimeout(() => {
      if (!shadow.activeElement || shadow.activeElement !== button) hideButton();
    }, 160);
  }, true);

  document.addEventListener("contextmenu", (event) => {
    const visual = VisualBrainCaptureTarget.find(event);
    const visualUrl = visual?.kind === "image" ? (visual.element.currentSrc || visual.element.src) : visual?.url;
    contextCapture = visual && !isVisualBrainUrl(location.href, backendOrigin) && !isVisualBrainUrl(visualUrl, backendOrigin)
      ? {capture:buildVisualCapture(visual), timestamp:Date.now()} : null;
    reportVisualBrainContext(!!visual && !contextCapture);
  }, true);
  window.addEventListener("visual-brain-boards-changed", () => chrome.runtime.sendMessage({type:"VB_REFRESH_BOARDS"}).catch(() => {}));

  window.addEventListener("scroll", () => {
    if (activeVisual?.element?.isConnected && button.style.display !== "none") positionButton(activeVisual.element);
  }, true);
  window.addEventListener("resize", () => {
    if (activeVisual?.element?.isConnected && button.style.display !== "none") positionButton(activeVisual.element);
  });

  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await saveActiveVisual();
  });

  async function saveActiveVisual() {
    if (!activeVisual?.element?.isConnected || button.disabled) {
      showToast("Hover an image, then press Option+Shift+S to save it.", false);
      return {ok:false,message:"Hover an image first."};
    }
    const capture = buildVisualCapture(activeVisual);
    button.disabled = true;
    button.style.visibility = "hidden";
    label.textContent = "Saving…";
    try {
      const result = await chrome.runtime.sendMessage({ type: "VB_CAPTURE", capture });
      showResult(result);
      return result;
    } catch (error) {
      const result = {ok:false,message:error?.message || "Could not reach Visual Brain."};
      showResult(result);
      return result;
    } finally {
      button.disabled = false;
      button.style.visibility = "visible";
      label.textContent = "Save to Visual Brain";
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "VB_QUICK_CAPTURE") {
      saveActiveVisual().then(sendResponse);
      return true;
    }
    if (message?.type === "VB_GET_CONTEXT_CAPTURE") {
      const fresh = contextCapture && Date.now() - contextCapture.timestamp < 10_000;
      sendResponse(fresh ? contextCapture.capture : null);
      return true;
    }
    if (message?.type === "VB_CAPTURE_SCREENSHOT_PREPARE") { host.style.visibility="hidden"; sendResponse({ok:true}); return true; }
    if (message?.type === "VB_CAPTURE_SCREENSHOT_RESTORE") { host.style.visibility="visible"; sendResponse({ok:true}); return true; }
    if (message?.type === "VB_CAPTURE_RESULT") showResult(message.result);
    return undefined;
  });

  function positionButton(image) {
    const rect = image.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) {
      hideButton();
      return;
    }
    const left = Math.min(Math.max(8, rect.right - 174), Math.max(8, window.innerWidth - 182));
    const top = Math.max(8, Math.min(rect.top + 8, window.innerHeight - 48));
    button.style.left = `${left}px`;
    button.style.top = `${top}px`;
    button.style.display = "inline-flex";
  }

  function hideButton() {
    button.style.display = "none";
    activeVisual = null;
    reportActiveCaptureTarget(false);
  }

  function screenCrop(element) {
    const rect=element.getBoundingClientRect();
    const x=Math.max(0,rect.left),y=Math.max(0,rect.top),right=Math.min(innerWidth,rect.right),bottom=Math.min(innerHeight,rect.bottom);
    return right-x >= 8 && bottom-y >= 8 ? {x,y,w:right-x,h:bottom-y,viewport_w:innerWidth,viewport_h:innerHeight} : null;
  }

  function buildVisualCapture(visual) {
    const element=visual.element;
    let capture;
    if (visual.kind === "image") capture=buildCapture(element);
    else {
      const title=element.getAttribute("title") || element.getAttribute("aria-label") || element.closest("a[title]")?.getAttribute("title") || "";
      capture={source_url:location.href,image_url:visual.url || "",image_candidates:visual.url ? [visual.url] : [],alt:title,title:title||document.title||""};
      if (visual.kind === "canvas") { try { capture.image_data_url=element.toDataURL("image/png"); } catch { /* Tainted canvas uses the visible clip fallback. */ } }
    }
    capture.screen_crop=screenCrop(element);
    return capture;
  }

  function buildCapture(image) {
    const imageCandidates = [];
    const add = (value) => {
      if (typeof value !== "string" || !value.trim()) return;
      try {
        const url = new URL(value.trim(), document.baseURI);
        if ((url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password) {
          const normalized = url.toString();
          if (!imageCandidates.includes(normalized)) imageCandidates.push(normalized);
        }
      } catch {
        // Ignore page attributes that are not image URLs.
      }
    };

    const scored = [];
    const addScored = (value, bonus = 0) => {
      if (typeof value !== "string" || !value.trim()) return;
      try {
        const url = new URL(value.trim(), document.baseURI);
        if (url.protocol !== "http:" && url.protocol !== "https:") return;
        if (url.username || url.password) return;
        const normalized = url.toString();
        const score = bonus + urlResolutionScore(normalized);
        const existing = scored.find((item) => item.url === normalized);
        if (existing) existing.score = Math.max(existing.score, score);
        else scored.push({ url: normalized, score });
      } catch {
        // Ignore invalid candidates.
      }
    };

    addScored(image.getAttribute("data-pin-media"), 100_000);
    addScored(image.getAttribute("data-original"), 90_000);
    addScored(image.getAttribute("data-src"), 20_000);
    addScored(image.getAttribute("data-lazy-src"), 15_000);
    addScored(image.getAttribute("data-url"), 10_000);

    const srcset = image.getAttribute("srcset") || image.getAttribute("data-srcset") || "";
    for (const candidate of parseSrcset(srcset, image.naturalWidth)) addScored(candidate.url, candidate.width);

    addScored(image.currentSrc, 100);
    addScored(image.getAttribute("src"), 0);

    const link = image.closest("a[href]");
    if (link) {
      addScored(link.getAttribute("data-pin-media"), 100_000);
      for (const name of ["data-original", "data-image-url", "data-media-url"]) addScored(link.getAttribute(name), 15_000);
    }

    scored.sort((a, b) => b.score - a.score);
    for (const item of scored.slice(0, 8)) add(item.url);
    const best = imageCandidates[0] || image.currentSrc || image.src;

    const alt = image.getAttribute("alt") || "";
    const title = image.getAttribute("title") || image.closest("a[title]")?.getAttribute("title") || "";
    const result = {
      source_url: location.href,
      image_url: best,
      image_candidates: imageCandidates,
      alt,
      title: title || alt || document.title || ""
    };
    const dataImage = image.currentSrc || image.src || "";
    if (/^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(dataImage)) result.image_data_url=dataImage;
    return result;
  }

  function parseSrcset(value, naturalWidth) {
    return value.split(",").map((part) => {
      const match = part.trim().match(/^(\S+)(?:\s+(\d+(?:\.\d+)?)(w|x))?$/i);
      if (!match) return null;
      const amount = Number(match[2] || 1);
      const width = match[3]?.toLowerCase() === "w" ? amount : amount * Math.max(naturalWidth || 1, 1);
      return { url: match[1], width };
    }).filter(Boolean);
  }

  function urlResolutionScore(value) {
    let score = 0;
    if (/\/originals\//i.test(value)) score += 1_000_000;
    const pinterestSize = value.match(/\/(\d{2,5})x(?:_|\/)/i);
    if (pinterestSize) score += Number(pinterestSize[1]) * 100;
    const dimensions = value.match(/(?:\/|[?&])(\d{2,5})x(\d{2,5})(?:_|\/|[?&]|$)/i);
    if (dimensions) score += Number(dimensions[1]) * Number(dimensions[2]);
    const explicit = value.match(/[?&](?:w|width|maxwidth)=(\d{2,5})/i);
    if (explicit) score += Number(explicit[1]) * 100;
    return score;
  }

  function showResult(result) {
    if (!result?.ok) {
      showToast(result?.message || "Could not save this image.", false);
      return;
    }
    showToast(result.message || "Saved to Visual Brain", true);
  }

  function showToast(message, success) {
    toast.textContent = String(message || "Capture finished.");
    toast.style.background = success ? "#173b2b" : "#4b2020";
    toast.style.display = "block";
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.style.display = "none"; }, 3_200);
  }
  }
})();

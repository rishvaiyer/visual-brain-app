// Find the visual under a pointer even when a link or overlay receives the event.
var VisualBrainCaptureTarget = (() => {
  function elements(event) {
    const doc = event.view?.document || document;
    const stacked = doc.elementsFromPoint?.(event.clientX, event.clientY) || [];
    const path = event.composedPath?.() || [];
    return [...new Set([...stacked, ...path])].filter(node => node instanceof Element && node !== doc.body && node !== doc.documentElement);
  }
  function backgroundUrl(node) {
    for (const part of [null,"::before","::after"]) {
      const css = getComputedStyle(node,part).backgroundImage || "";
      const match = css.match(/url\((?:"([^"]+)"|'([^']+)'|([^)]*))\)/i);
      if (match) return (match[1] || match[2] || match[3] || "").trim();
    }
    return "";
  }
  function find(event) {
    const nodes = elements(event);
    for (const node of nodes) if (node instanceof HTMLImageElement) return {kind:"image",element:node};
    for (const node of nodes.slice(0, 5)) {
      const child = node.querySelector?.("picture img, img");
      if (child instanceof HTMLImageElement) return {kind:"image",element:child};
    }
    for (const node of nodes) {
      if (node instanceof HTMLCanvasElement) return {kind:"canvas",element:node};
      if (node instanceof SVGSVGElement) return {kind:"svg",element:node};
    }
    for (const node of nodes.slice(0, 6)) {
      const url = backgroundUrl(node);
      if (url) return {kind:"background",element:node,url};
    }
    return null;
  }
  return {find};
})();

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor() { this.background = "none"; this.children = []; }
  querySelector() { return this.children.find(child=>child instanceof HTMLImageElement) || null; }
}
class HTMLImageElement extends Element { constructor(src) { super(); this.currentSrc=src;this.src=src; } }
class HTMLCanvasElement extends Element {}
class SVGSVGElement extends Element {}

const source=fs.readFileSync(path.join(__dirname,"..","extension","capture-target.js"),"utf8");
const context={Element,HTMLImageElement,HTMLCanvasElement,SVGSVGElement,getComputedStyle:el=>({backgroundImage:el.background}),URL};
vm.createContext(context);vm.runInContext(source,context);
const find=context.VisualBrainCaptureTarget.find;
const image=new HTMLImageElement("https://example.com/image.jpg"), overlay=new Element();
let event={clientX:30,clientY:40,composedPath:()=>[overlay],view:{document:{elementsFromPoint:()=>[overlay,image]}}};
assert.equal(find(event).element,image,"Overlay link should resolve the image beneath it");
overlay.background='url("https://example.com/background.webp")';
event.view.document.elementsFromPoint=()=>[overlay];
assert.equal(find(event).url,"https://example.com/background.webp","CSS background should be capturable");
overlay.background="none";overlay.children=[image];
assert.equal(find(event).element,image,"Linked tile should resolve its child image");
overlay.children=[];
assert.equal(find(event),null,"Blank page area should not select an unrelated image");
console.log("PASS linked, overlaid, CSS background, and blank targets");

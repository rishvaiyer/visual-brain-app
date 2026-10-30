const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function main() {
  const created = [];
  const listeners = {installed:null,startup:null,clicked:null,message:null};
  const chrome = {
    storage:{local:{get:async()=>({settings:{}})}},
    contextMenus:{removeAll:async()=>{created.length=0;},create:entry=>created.push(entry),update:async()=>{},onClicked:{addListener:fn=>listeners.clicked=fn}},
    runtime:{onInstalled:{addListener:fn=>listeners.installed=fn},onStartup:{addListener:fn=>listeners.startup=fn},onMessage:{addListener:fn=>listeners.message=fn}},
    commands:{onCommand:{addListener:()=>{}}},
    tabs:{onActivated:{addListener:()=>{}},onUpdated:{addListener:()=>{}},onRemoved:{addListener:()=>{}},
      query:async()=>[{id:1,url:"https://example.com"}],get:async()=>({id:1,url:"https://example.com"}),sendMessage:async()=>null}
  };
  const source=fs.readFileSync(path.join(__dirname,"..","extension","background.js"),"utf8");
  vm.runInNewContext(source,{chrome,URL,Promise,fetch:async()=>{throw Error("unexpected fetch")},TextEncoder,Blob,btoa,Uint8Array}, {filename:"background.js"});
  await new Promise(resolve=>setImmediate(resolve));
  const parent=created.find(entry=>entry.id==="visual-brain-save-image");
  assert(parent,"Save to Visual Brain menu missing");
  assert(parent.contexts.includes("link"),"Linked image tiles must show the right-click menu");
  assert(parent.contexts.includes("page"),"CSS background tiles must show the right-click menu");
  assert(created.some(entry=>entry.id==="visual-brain-default"),"Everything + Library option missing");
  console.log("PASS right-click menu covers image, link, and page visual tiles");
}

main().catch(error=>{console.error(error.message);process.exitCode=1});

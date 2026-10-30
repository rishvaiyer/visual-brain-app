const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");

async function main() {
  const screenshot="data:image/png;base64,iVBORw0KGgo=";
  let posted=null,captures=0;
  const chrome={
    storage:{local:{get:async()=>({settings:{pairingKey:"test-key",backendUrl:"http://127.0.0.1:8777"}})}},
    contextMenus:{removeAll:async()=>{},create:()=>{},update:async()=>{},onClicked:{addListener:()=>{}}},
    runtime:{onInstalled:{addListener:()=>{}},onStartup:{addListener:()=>{}},onMessage:{addListener:()=>{}}},
    commands:{onCommand:{addListener:()=>{}}},
    tabs:{onActivated:{addListener:()=>{}},onUpdated:{addListener:()=>{}},onRemoved:{addListener:()=>{}},
      sendMessage:async()=>({ok:true}),query:async()=>[{id:1,url:"https://example.com/page"}],get:async()=>({id:1,url:"https://example.com/page"}),
      captureVisibleTab:async()=>{captures++;return screenshot}}
  };
  const fetch=async (url,options={})=>{
    if(String(url).endsWith("/api/boards-list")) return {ok:true,json:async()=>({boards:[{id:"main",name:"Everything"}]})};
    if(String(url).endsWith("/api/capture")) {posted=JSON.parse(options.body);return {ok:true,json:async()=>({duplicate:false})};}
    throw new Error("Image host blocked bytes");
  };
  const context={chrome,fetch,URL,Promise,TextEncoder,Blob,btoa,Uint8Array};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,"..","extension","background.js"),"utf8"),context);
  const crop={x:10,y:20,w:100,h:90,viewport_w:800,viewport_h:600};
  const result=await context.performCapture({image_url:"https://example.com/image.jpg",screen_crop:crop},"https://example.com/page",null,{tab:{id:1,active:true,windowId:2},frameId:0});
  assert.equal(result.ok,true);
  assert.equal(captures,1);
  assert.equal(posted.image_data_url,screenshot);
  assert.equal(posted.image_url,"");
  assert.equal(posted.screen_crop.w,100);
  assert.match(result.message,/visible image clip/i);
  console.log("PASS blocked source falls back to a local visible clip");
}
main().catch(error=>{console.error(error);process.exitCode=1});

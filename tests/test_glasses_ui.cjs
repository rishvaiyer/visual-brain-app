const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'features.js'),'utf8').split('/* Free 3D boards, imported models, and at-scale room placement. */')[0];
function setup({demo=false,canEdit=true,protocol='https:',details={private_library:true,voice_available:false,transport:'iphone-meta-dat',companion_scheme:'visualbrain'}}={}) {
 const nodes = new Map();
 function node(selector) { if (!nodes.has(selector)) nodes.set(selector,{open:false,hidden:false,disabled:false,innerHTML:'',textContent:'',srcObject:null,events:{},attrs:{},classList:{remove(){}},addEventListener(name,fn){(this.events[name]??=[]).push(fn)},showModal(){this.open=true},close(){this.open=false;for(const fn of this.events.close??[])fn()},setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k];if(k==='href')delete this.href},querySelectorAll(){return []},async play(){},click(){for(const fn of this.events.click??[])fn({})}}); return nodes.get(selector); }
 let requestCount=0;
 const context={URL,AbortController,console,state:{key:'',canEdit,capabilities:{demo},boards:[],items:[],selected:new Set()},location:{protocol,origin:protocol+'//board.example'},document:{querySelector:node},navigator:{mediaDevices:{}},window:{addEventListener(){}},request:async()=>{requestCount++;return details},asset:()=>undefined,assetDisplayLabel:()=>'',esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),fetch:async()=>({ok:true,json:async()=>({matches:[]})})};
 vm.createContext(context);vm.runInContext(source,context);return {context,node,get requestCount(){return requestCount}};
}
(async()=>{
 for (const config of [{demo:true},{canEdit:false},{protocol:'http:'},{details:{private_library:false}},{details:{private_library:true,transport:'other',companion_scheme:'visualbrain'}}]) {
  const env=setup(config);await env.context.openPointReopen();assert.equal(env.node('#point-companion-open').hidden,true);assert.equal(env.node('#point-companion-open').href,undefined);
  if(config.demo||config.canEdit===false)assert.equal(env.requestCount,0);
 }
 const owner=setup();await owner.context.openPointReopen();assert.equal(owner.node('#point-companion-open').hidden,false);assert.equal(new URL(owner.node('#point-companion-open').href).searchParams.get('server'),'https://board.example');
 let resolveCamera;let stopped=0;owner.context.navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>resolveCamera=resolve);
 const pendingCamera=owner.context.startPointCamera();owner.node('#point-dialog').close();resolveCamera({getTracks:()=>[{stop(){stopped++}}]});await pendingCamera;assert.equal(stopped,1);assert.equal(owner.node('#point-video').srcObject,null);
 await owner.context.openPointReopen();let resolveFetch;owner.context.fetch=()=>new Promise(resolve=>resolveFetch=resolve);const pendingMatch=owner.context.pointMatch('data:image/jpeg;base64,test');assert.equal(owner.node('#point-choose').disabled,true);owner.node('#point-dialog').close();await owner.context.openPointReopen();const reopenedStatus=owner.node('#point-status').textContent;resolveFetch({ok:true,json:async()=>({matches:[]})});await pendingMatch;assert.equal(owner.node('#point-status').textContent,reopenedStatus);assert.equal(owner.node('#point-choose').disabled,false);
 owner.context.showPointMatches([{id:'new-image',title:'<script>untrusted</script>',boards:['a&b'],board_names:['New board']}]);assert.ok(owner.node('#point-results').innerHTML.includes('&lt;script&gt;'));assert.ok(owner.node('#point-results').innerHTML.includes('/board?board=a%26b'));assert.ok(owner.node('#point-results').innerHTML.includes('New board'));
 console.log('Companion privacy gate, camera close race, canceled matches and escaped metadata passed.');
})().catch(error=>{console.error(error);process.exitCode=1});

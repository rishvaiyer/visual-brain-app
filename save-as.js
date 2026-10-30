// Chrome's file picker opens during the click, before the image or ZIP is prepared.
function fileName(value) { return String(value || "Board").replace(/[\\/:*?"<>|]/g, "-").trim().slice(0, 80) || "Board"; }
function picker(name, extension, mime) {
  if (!window.showSaveFilePicker) return null;
  return window.showSaveFilePicker({suggestedName:name, types:[{description:extension === ".zip" ? "ZIP archive" : "Image file", accept:{[mime]:[extension]}}]});
}
async function writeFile(blob, name, handle) {
  if (handle) { const stream = await handle.createWritable(); await stream.write(blob); await stream.close(); return; }
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 60_000);
}
function exportError(error) { if (error?.name !== "AbortError") toast(error?.message || "Could not save file"); }
async function saveOriginalImage(assetId) {
  const a=asset(assetId); if(!a)return;
  const name=originalFileName(a),extension="."+name.split(".").pop();
  try {
    const handle=await picker(name,extension,a.mime);
    const response=await fetch(`/api/assets/${a.id}`);
    if(!response.ok)throw new Error("Could not read the saved image");
    await writeFile(await response.blob(),name,handle);
    toast("Original image saved");
  } catch(error) { exportError(error); }
}
async function saveAllData() {
  if ($("#save-dialog").open) $("#save-dialog").close();
  const name = "visual-brain-backup.zip";
  try {
    const handle = await picker(name, ".zip", "application/zip");
    const response = await fetch("/api/export", {headers:{"X-Board-Key":state.key}});
    if (!response.ok) throw new Error("Backup failed");
    await writeFile(await response.blob(), name, handle);
    toast("All data saved");
  } catch (error) { exportError(error); }
}
async function saveBoardImage() {
  if ($("#save-dialog").open) $("#save-dialog").close();
  const name = `${fileName(currentBoard().name)}.png`;
  try {
    const handle = await picker(name, ".png", "image/png");
    const blob = await renderBoardPng();
    await writeFile(blob, name, handle);
    toast("Board image saved");
  } catch (error) { exportError(error); }
}
function canvasText(ctx, value, x, y, maxWidth, lineHeight) {
  let row = 0;
  for (const paragraph of String(value || "").split("\n")) {
    let line = "";
    for (const word of paragraph.split(/\s+/)) {
      const test = line ? `${line} ${word}` : word;
      if (line && ctx.measureText(test).width > maxWidth) { ctx.fillText(line, x, y + row * lineHeight); row++; line = word; }
      else line = test;
    }
    ctx.fillText(line, x, y + row * lineHeight); row++;
  }
}
function paintShape(ctx, s) {
  const w=s.w,h=s.h,stroke=safeColor(s.stroke,"#4d684f"),fill=safeColor(s.fill,"#c8d4bb"),line=Math.max(1,Math.min(30,Number(s.strokeWidth)||2));
  ctx.strokeStyle=stroke; ctx.fillStyle=fill; ctx.lineWidth=line; ctx.lineJoin="round"; ctx.lineCap="round";
  ctx.beginPath();
  if(s.shape === "ellipse") ctx.ellipse(w/2,h/2,Math.max(1,w/2-line),Math.max(1,h/2-line),0,0,Math.PI*2);
  else if(s.shape === "triangle") { ctx.moveTo(w/2,line); ctx.lineTo(w-line,h-line); ctx.lineTo(line,h-line); ctx.closePath(); }
  else if(s.shape === "line" || s.shape === "arrow") { ctx.moveTo(line,h/2); ctx.lineTo(w-line-(s.shape === "arrow" ? 12 : 0),h/2); ctx.stroke(); if(s.shape === "arrow") { ctx.beginPath(); ctx.moveTo(w-line,h/2); ctx.lineTo(w-line-16,h/2-11); ctx.lineTo(w-line-16,h/2+11); ctx.closePath(); ctx.fillStyle=stroke;ctx.fill(); } return; }
  else ctx.rect(line,line,Math.max(1,w-2*line),Math.max(1,h-2*line));
  ctx.fill(); ctx.stroke();
}
function paintBoardBackground(ctx, width, height, left, top, background) {
  ctx.fillStyle="#f5f3ed"; ctx.fillRect(0,0,width,height);
  if (background === "plain") return;
  const step=24;
  const startX=Math.ceil(left/step)*step-left, startY=Math.ceil(top/step)*step-top;
  if (background === "dots") {
    ctx.fillStyle="#d4d1c6";
    for(let x=startX;x<width;x+=step) for(let y=startY;y<height;y+=step) ctx.fillRect(x,y,1.5,1.5);
    return;
  }
  ctx.lineWidth=1;
  if(background === "grid" || background === "graph") {
    for(let x=startX;x<width;x+=step) { const world=Math.round(left+x);ctx.strokeStyle=background === "graph" ? (world%120===0 ? "#b9c8b8" : "#dbe4d8") : "#d9d7cb";ctx.beginPath();ctx.moveTo(x+.5,0);ctx.lineTo(x+.5,height);ctx.stroke(); }
  }
  if(background === "grid" || background === "graph" || background === "lined") {
    for(let y=startY;y<height;y+=step) { const world=Math.round(top+y);ctx.strokeStyle=background === "graph" ? (world%120===0 ? "#b9c8b8" : "#dbe4d8") : background === "lined" ? "#d6dacb" : "#d9d7cb";ctx.beginPath();ctx.moveTo(0,y+.5);ctx.lineTo(width,y+.5);ctx.stroke(); }
  }
}
async function renderBoardPng() {
  const all = [...currentItems()].sort((a,b)=>(a.state.z||0)-(b.state.z||0));
  if (!all.length) throw new Error("This board has nothing to save yet.");
  await loadFonts();
  await document.fonts.ready;
  const left=Math.min(...all.map(i=>i.state.x))-48, top=Math.min(...all.map(i=>i.state.y))-48;
  const right=Math.max(...all.map(i=>i.state.x+i.state.w))+48, bottom=Math.max(...all.map(i=>i.state.y+i.state.h))+48;
  const width=Math.ceil(right-left),height=Math.ceil(bottom-top);
  if(width>12000 || height>12000 || width*height>48_000_000) throw new Error("Board is too large for one PNG. Save all data as ZIP instead.");
  const canvas=document.createElement("canvas");canvas.width=width;canvas.height=height;
  const ctx=canvas.getContext("2d");paintBoardBackground(ctx,width,height,left,top,view().background || "dots");
  const images=new Map();
  for(const i of all.filter(i=>i.kind === "image")) if(!images.has(i.asset_id)) {
    const response=await fetch(`/api/assets/${i.asset_id}`);if(!response.ok)throw new Error("A saved image could not be read");
    images.set(i.asset_id,await createImageBitmap(await response.blob()));
  }
  for(const i of all) {
    const s=i.state,w=Number(s.w)||1,h=Number(s.h)||1;
    ctx.save();ctx.translate(s.x-left+w/2,s.y-top+h/2);ctx.rotate((Number(s.rotation)||0)*Math.PI/180);ctx.translate(-w/2,-h/2);
    if(i.kind === "image") {
      const img=images.get(i.asset_id),c=s.crop||{x:0,y:0,w:1,h:1};
      const cropW=Math.max(.01,c.w)*img.width,cropH=Math.max(.01,c.h)*img.height,scale=Math.max(w/cropW,h/cropH);
      ctx.beginPath();ctx.rect(0,0,w,h);ctx.clip();ctx.drawImage(img,(w-cropW*scale)/2-c.x*img.width*scale,(h-cropH*scale)/2-c.y*img.height*scale,img.width*scale,img.height*scale);
    } else if(i.kind === "text") {
      const size=Math.max(8,Math.min(200,Number(s.fontSize)||22));ctx.fillStyle=safeColor(s.color,"#30382f");ctx.font=`${s.fontWeight||400} ${size}px ${s.fontFamily||"Avenir Next, sans-serif"}`;ctx.textBaseline="top";ctx.textAlign=s.textAlign||"left";
      canvasText(ctx,s.text||"Text",s.textAlign === "center" ? w/2 : s.textAlign === "right" ? w-5 : 5,5,w-10,size*1.25);
    } else if(i.kind === "frame") {
      ctx.fillStyle="#e9ede5";ctx.fillRect(0,0,w,h);ctx.strokeStyle="#707a69";ctx.lineWidth=2;ctx.strokeRect(1,1,w-2,h-2);ctx.fillStyle="#4d594b";ctx.font="600 13px Avenir Next, sans-serif";ctx.textBaseline="top";canvasText(ctx,s.text||"Frame",8,8,w-16,17);
    } else if(i.kind === "shape") paintShape(ctx,s);
    else if(i.kind === "drawing") { ctx.beginPath();(s.points||[]).forEach(([x,y],index)=>index ? ctx.lineTo(x,y) : ctx.moveTo(x,y));ctx.strokeStyle=s.color||"#333b30";ctx.lineWidth=3;ctx.lineCap="round";ctx.lineJoin="round";ctx.stroke(); }
    ctx.restore();
  }
  for(const img of images.values()) img.close();
  return await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob ? resolve(blob) : reject(new Error("Could not create PNG")),"image/png"));
}

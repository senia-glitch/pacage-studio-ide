import {$,esc,S,hooks,KIND_NAMES,methodOfEndpoint,schedulePersist,applyTransform} from "./core.js";

/* Панель «Структура» слева на «Карте проекта»: иерархия домен → HTTP-метод →
   эндпоинт → потомки, синхронизированная с canvas-картой. Клик по строке
   выделяет узел (как клик по карточке) и плавно центрирует камеру; если у узла
   есть потомки — они вписываются в кадр (отдаление только когда не влезают).
   Состояние (открытость/ширина панели, свёрнутые ветки) живёт в localStorage
   вместе с остальным UI-состоянием через uiSnapshot/restoreUiState в core.js. */

const MARGIN=72,MIN_W=220,MAX_W=520;

/* Режим дерева: "all" — всё; "fact" — только фактические узлы; "plan" — только
   планируемые (узлы «план», плановые правки, переименования, плановые домены).
   Панель не зависит от режима канваса: группировка и состав определяются
   только S.treeMode. Каркас (домены, HTTP-методы, узлы-предки) показывается,
   лишь если в его поддереве есть узлы текущего режима. */

/* ---------- иерархия: повторяет модель карты (mapview.model) ---------- */
function effectiveDomains(n){
  return String(n.domain||"").split(",").map(v=>v.trim()).filter(Boolean).map(d=>S.treeMode==="plan"&&S.G.domain_changes?.[d]?.name||d);
}
function treeData(){
  const nodes=(S.G.nodes||[]).filter(n=>n&&n.id);
  const byId=new Map(nodes.map(n=>[n.id,n]));
  // Ровно как на карте: ребро «tests» инвертировано (тест — потомок).
  const edges=(S.G.edges||[]).map(e=>e.kind==="tests"?{from_node:e.to_node,to_node:e.from_node}:{from_node:e.from_node,to_node:e.to_node});
  // Один родитель на узел (как на карте): первая встретившаяся связь.
  const childMap=new Map(nodes.map(n=>[n.id,[]])),hasParent=new Set();
  for(const e of edges){
    const p=e.from_node,c=e.to_node;
    if(!p||!c||p===c||!childMap.has(p))continue;
    const child=byId.get(c);
    if(!child||child.kind==="endpoint"||child.kind==="method"||child.kind==="domain")continue;
    if(hasParent.has(c))continue;
    hasParent.add(c);childMap.get(p).push(c);
  }
  const synth=new Map(),subtreeKids=new Map();
  for(const n of nodes){
    const pc=n.planned_change;
    if(!pc||!(pc.note||pc.acceptance?.length))continue;
    const id=`@change:${n.id}`;
    synth.set(id,{id,kind:"change",label:"Плановая правка",planned:true,sourceNodeId:n.id});
    if(!hasParent.has(id)){childMap.get(n.id)?.push(id);hasParent.add(id)}
  }
  // Группы доменов и HTTP-методов — как на карте: домены создаются по всем
  // доменам эндпоинта, но ветка эндпоинта живёт только под его основным
  // доменом (первым из перечисления, как primaryDomain в positionGraph).
  const domainEntries=new Map();
  const ensureEntry=(d,method)=>{
    const plannedDomain=(S.G.planned_domains||[]).find(item=>item.name===d);
    const did=plannedDomain?`@domain-plan:${plannedDomain.id}`:`@domain:${d}`;
    const rename=Object.entries(S.G.domain_changes||{}).find(([oldName,v])=>v?.name===d);
    if(!domainEntries.has(did))domainEntries.set(did,{label:d,rename,plannedDomain,methods:new Map()});
    const entry=domainEntries.get(did);
    if(!entry.methods.has(method))entry.methods.set(method,[]);
    return entry;
  };
  for(const endpoint of nodes.filter(n=>n.kind==="endpoint")){
    const method=methodOfEndpoint(endpoint)||"Метод не указан";
    const domains=effectiveDomains(endpoint);
    for(const d of (domains.length?domains:["Без домена"]))ensureEntry(d,method);
    ensureEntry(domains[0]||"Без домена",method).methods.get(method).push(endpoint);
  }
  if(!domainEntries.has("@domain:Без домена"))domainEntries.set("@domain:Без домена",{label:"Без домена",rename:null,plannedDomain:null,methods:new Map()});
  for(const entry of domainEntries.values()){
    const items=[...entry.methods.values()].flat();
    entry.plannedGroup=!!entry.rename||!!entry.plannedDomain||(items.length>0&&items.every(n=>n.planned));
  }
  const domainKeys=[...domainEntries.keys()].sort((a,b)=>{
    const la=domainEntries.get(a).label,lb=domainEntries.get(b).label;
    return ((la==="Без домена")?1:0)-((lb==="Без домена")?1:0)||la.localeCompare(lb,"ru");
  });
  const domainChange=new Map();
  for(const [source,change] of Object.entries(S.G.domain_changes||{})){
    const did=`@domain:${source}`;
    if(!domainEntries.has(did))continue;
    const id=`@domain-change:${source}`;
    synth.set(id,{id,kind:"domain",label:change?.name||source,synthetic:true,sourceDomain:source,planned:true});
    if(!domainChange.has(did))domainChange.set(did,[]);
    domainChange.get(did).push(id);
  }
  for(const did of domainKeys){
    const entry=domainEntries.get(did);
    synth.set(did,{id:did,kind:"domain",label:entry.label,synthetic:true,planned:entry.plannedGroup,domainPlanId:entry.plannedDomain?.id||""});
    const methodIds=[];
    for(const method of [...entry.methods.keys()].sort((a,b)=>a.localeCompare(b,"ru"))){
      const mid=`@method:${did}:${method}`;
      synth.set(mid,{id:mid,kind:"method",label:method,synthetic:true,planned:false});
      subtreeKids.set(mid,entry.methods.get(method).map(n=>n.id));
      methodIds.push(mid);
    }
    subtreeKids.set(did,[...(domainChange.get(did)||[]),...methodIds]);
  }
  for(const d of S.G.planned_domains||[]){
    const did=`@domain-plan:${d.id}`;
    if(!domainEntries.has(did)){synth.set(did,{id:did,kind:"domain",label:d.name,synthetic:true,planned:true,domainPlanId:d.id});subtreeKids.set(did,[])}
  }
  // Узлы вне доменной структуры (нет родителя и это не эндпоинт) — один
  // сворачиваемый раздел «Свободные узлы» в конце дерева.
  const freeRoots=nodes.filter(n=>!hasParent.has(n.id)&&n.kind!=="endpoint").map(n=>n.id);
  const freeSection=freeRoots.length?{id:"@free:",kind:"free",label:"Свободные узлы",synthetic:true,section:true}:null;
  if(freeSection){synth.set(freeSection.id,freeSection);subtreeKids.set(freeSection.id,freeRoots)}
  const labelOf=id=>byId.get(id)||synth.get(id)||{id,kind:"",label:id};
  const byLabel=(a,b)=>String(labelOf(a).label||"").localeCompare(String(labelOf(b).label||""),"ru");
  for(const list of childMap.values())list.sort(byLabel);
  for(const list of subtreeKids.values())list.sort(byLabel);
  const kidsOf=id=>synth.has(id)?(subtreeKids.get(id)||[]):(childMap.get(id)||[]);
  /* Узел режима: само значение совпадает с выбранным режимом панели.
     Каркасные синтетические узлы (домены, HTTP-методы, раздел свободных
     узлов) сами узлами режима не считаются; плановые пометки (@change,
     переименования, плановые домены) относятся к «план». */
  const selfMatch=node=>{
    if(S.treeMode==="all")return true;
    const id=typeof node.id==="string"?node.id:"";
    const marker=id.startsWith("@change:")||id.startsWith("@domain-change:")||id.startsWith("@domain-plan:");
    if(id.startsWith("@domain:")||id.startsWith("@method:")){
      // каркас; плановый домен (переименование или новый) — часть «плана»
      return S.treeMode==="plan"&&node.planned===true;
    }
    if(id.startsWith("@free:"))return false;
    if(marker)return S.treeMode==="plan";
    return S.treeMode==="fact"?!node.planned:!!node.planned;
  };
  const wanted=new Map();
  const wantRec=id=>{
    if(wanted.has(id))return wanted.get(id);
    wanted.set(id,false);
    let found=selfMatch(byId.get(id)||synth.get(id)||{id});
    for(const kid of kidsOf(id))if(wantRec(kid))found=true;
    wanted.set(id,found);
    return found;
  };
  // Корни: домены, отдельно стоящие плановые домены, затем раздел «Свободные узлы».
  const roots=[...domainKeys];
  for(const d of S.G.planned_domains||[]){const did=`@domain-plan:${d.id}`;if(!domainEntries.has(did))roots.push(did)}
  if(freeSection)roots.push(freeSection.id);
  const rows=[];
  const walk=(id,depth,stack)=>{
    rows.push({id,depth,node:labelOf(id),kids:kidsOf(id)});
    if(S.treeCollapsed.has(id))return;
    for(const kid of kidsOf(id)){
      if(stack.has(kid))continue;
      if(!wantRec(kid))continue;
      stack.add(kid);walk(kid,depth+1,stack);stack.delete(kid);
    }
  };
  for(const root of roots){
    if(!wantRec(root))continue;
    walk(root,0,new Set([root]));
  }
  return rows;
}

/* ---------- камера ---------- */
let cameraAnim=null;
function stopCameraAnim(){if(cameraAnim){cancelAnimationFrame(cameraAnim.raf);cameraAnim=null}}
function animateCamera(canvas,target){
  stopCameraAnim();
  const from={x:S.mapView.x,y:S.mapView.y,k:S.mapView.k};
  const dx=target.x-from.x,dy=target.y-from.y,dk=target.k-from.k;
  const distance=Math.hypot(dx,dy)+Math.abs(dk)*500;
  const reduced=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const duration=reduced||!Number.isFinite(distance)||distance<2?0:Math.round(Math.min(420,180+distance*0.3));
  if(!duration){S.mapView.x=target.x;S.mapView.y=target.y;S.mapView.k=target.k;applyTransform("mapView",S.mapView);return}
  cameraAnim={t0:performance.now(),raf:0};
  const ease=t=>1-Math.pow(1-t,3);
  const step=now=>{
    if(!cameraAnim)return;
    if(canvas.classList.contains("grabbing")){cameraAnim=null;return}
    const t=Math.min(1,(now-cameraAnim.t0)/duration),e=ease(t);
    S.mapView.x=from.x+dx*e;S.mapView.y=from.y+dy*e;S.mapView.k=from.k+dk*e;
    applyTransform("mapView",S.mapView);
    if(t<1)cameraAnim.raf=requestAnimationFrame(step);else cameraAnim=null;
  };
  cameraAnim.raf=requestAnimationFrame(step);
}
/* Гибридное вписывание: камера центрируется на объединённой рамке узла и его
   потомков; масштаб плавно уменьшается, только когда рамка не влезает в кадр. */
function focusNode(id){
  const canvas=$("mapCanvas");
  if(!canvas||S.tab!=="map")return;
  const entry=(S.mapViewNodes||{})[id];
  const boxes=S.mapBoxes||{};
  const box=entry&&boxes[entry.viewId];
  if(!box||!Number.isFinite(box.x))return;
  const frame={left:box.x,top:box.y,right:box.x+box.w,bottom:box.y+box.h};
  let hasDescendants=false;
  const seen=new Set([entry.viewId]),stack=[entry.viewId];
  while(stack.length){
    const current=stack.pop();
    for(const kid of (S.mapChildren&&S.mapChildren.get(current))||[]){
      if(seen.has(kid))continue;
      seen.add(kid);
      const kb=boxes[kid];
      if(kb&&Number.isFinite(kb.x)){
        frame.left=Math.min(frame.left,kb.x);frame.top=Math.min(frame.top,kb.y);
        frame.right=Math.max(frame.right,kb.x+kb.w);frame.bottom=Math.max(frame.bottom,kb.y+kb.h);
        hasDescendants=true;
      }
      stack.push(kid);
    }
  }
  const w=Math.max(1,frame.right-frame.left),h=Math.max(1,frame.bottom-frame.top);
  const cw=canvas.clientWidth||900,ch=canvas.clientHeight||600;
  const fitK=Math.min(2.5,Math.max(0.2,Math.min((cw-2*MARGIN)/w,(ch-2*MARGIN)/h)));
  const k=hasDescendants?Math.min(S.mapView.k,fitK):S.mapView.k;
  animateCamera(canvas,{x:(cw-w*k)/2-frame.left*k,y:(ch-h*k)/2-frame.top*k,k});
}

/* ---------- панель ---------- */
function kindLabel(node){
  return node.kind==="method"?"HTTP-метод":node.kind==="domain"?"домен":node.kind==="change"?"план правки":KIND_NAMES[node.kind]||node.kind||"";
}
function ensureInitCollapsed(){
  if(S.treeCollapsedInit)return;
  const nodes=(S.G&&(S.G.nodes||[]))||[];
  if(!nodes.length)return;
  S.treeCollapsedInit=true;
  for(const n of nodes)if(n.kind==="endpoint")S.treeCollapsed.add(n.id);
  schedulePersist();
}
/* Раздел «Свободные узлы» сворачивается по умолчанию; однажды увиденный
   пользователем флаг запоминается, чтобы его выбор разворота сохранялся. */
function ensureFreeSectionDefault(){
  if(S.treeFreeInit)return;
  S.treeFreeInit=true;
  if(!S.treeCollapsed.has("@free:"))S.treeCollapsed.add("@free:");
  schedulePersist();
}
function renderTree(){
  const body=$("treeBody");
  if(!body||!S.G)return;
  const keep=body.scrollTop;
  ensureInitCollapsed();
  const rows=treeData();
  if(rows.some(row=>row.node.section))ensureFreeSectionDefault();
  const html=rows.map(row=>{
    const node=row.node,collapsed=S.treeCollapsed.has(row.id),leaf=!row.kids.length;
    const expanded=leaf?"":` aria-expanded="${collapsed?"false":"true"}"`;
    const rowCls=node.section?" tree-section":"";
    return `<div class="tree-row${rowCls}${row.id===S.selectedNode?" sel":""}" role="treeitem" data-id="${esc(row.id)}" tabindex="0"${expanded} style="padding-left:${8+row.depth*13}px">`
      +`<button type="button" class="tree-twist${leaf?" leaf":""}" tabindex="-1" title="${collapsed?"Развернуть":"Свернуть"}">${collapsed?"▸":"▾"}</button>`
      +(node.section?"":`<span class="tree-chip">${esc(kindLabel(node))}</span>`)
      +(node.planned?'<span class="chip todo">план</span>':"")
      +`<span class="t" title="${esc(node.label||"")}">${esc(node.label||"")}</span></div>`;
  });
  body.innerHTML=rows.length?html.join(""):'<div class="hint tree-empty">Узлов нет. Добавьте эндпоинт или выполните «Обновить код».</div>';
  body.scrollTop=keep;
}
function toggleCollapsed(id){
  if(S.treeCollapsed.has(id))S.treeCollapsed.delete(id);else S.treeCollapsed.add(id);
  schedulePersist();renderTree();
}
function openNode(id){
  S.selectedNode=id;
  hooks.renderInspector?.();
  schedulePersist();
  hooks.renderMap?.();
  focusNode(id);
}
function applyPanelState(){
  document.documentElement.style.setProperty("--tree-width",`${Math.round(S.treeWidth)}px`);
  document.body.classList.toggle("tree-hidden",!S.treeOpen);
}
function applyTreeModes(){
  document.querySelectorAll("#treeModes [data-tree-mode]").forEach(b=>b.classList.toggle("on",b.dataset.treeMode===S.treeMode));
}
function initTreeModes(){
  applyTreeModes();
  document.querySelectorAll("#treeModes [data-tree-mode]").forEach(b=>b.addEventListener("click",()=>{
    const mode=b.dataset.treeMode;
    if(!["all","fact","plan"].includes(mode))return;
    S.treeMode=mode;
    applyTreeModes();
    renderTree();
    schedulePersist();
  }));
}
function initTree(){
  applyPanelState();
  initTreeModes();
  $("treeFoldAll")?.addEventListener("click",()=>{
    let folded=false;
    for(const row of treeData())
      if(row.depth>0&&row.kids.length&&!row.node.section&&!S.treeCollapsed.has(row.id)){S.treeCollapsed.add(row.id);folded=true}
    if(!folded)return;
    schedulePersist();renderTree();
  });
  $("treeCollapse")?.addEventListener("click",()=>{S.treeOpen=false;applyPanelState();schedulePersist()});
  $("treeTab")?.addEventListener("click",()=>{S.treeOpen=true;applyPanelState();schedulePersist()});
  const resize=$("treeResize");
  if(resize){
    let startX=0,startW=0;
    resize.addEventListener("pointerdown",ev=>{startX=ev.clientX;startW=S.treeWidth;resize.setPointerCapture(ev.pointerId);document.body.classList.add("resizing-tree")});
    resize.addEventListener("pointermove",ev=>{
      if(!resize.hasPointerCapture(ev.pointerId))return;
      S.treeWidth=Math.max(MIN_W,Math.min(MAX_W,startW+(ev.clientX-startX)));
      document.documentElement.style.setProperty("--tree-width",`${Math.round(S.treeWidth)}px`);
    });
    resize.addEventListener("pointerup",ev=>{if(resize.hasPointerCapture(ev.pointerId))resize.releasePointerCapture(ev.pointerId);document.body.classList.remove("resizing-tree");schedulePersist()});
  }
  const body=$("treeBody");
  if(body){
    body.addEventListener("click",ev=>{
      const rowEl=ev.target.closest(".tree-row");
      if(!rowEl)return;
      const id=rowEl.dataset.id;
      if(!id)return;
      const twist=ev.target.closest(".tree-twist");
      if(rowEl.classList.contains("tree-section")||(twist&&!twist.classList.contains("leaf"))){toggleCollapsed(id);return}
      openNode(id);
    });
    body.addEventListener("keydown",ev=>{
      if((ev.key!=="Enter"&&ev.key!==" ")||ev.target!==ev.target.closest?.(".tree-row"))return;
      ev.preventDefault();
      const id=ev.target.dataset.id;
      if(!id)return;
      if(ev.target.classList.contains("tree-section"))toggleCollapsed(id);
      else openNode(id);
    });
  }
  const canvas=$("mapCanvas");
  if(canvas){
    canvas.addEventListener("wheel",stopCameraAnim,{capture:true,passive:true});
    canvas.addEventListener("mousedown",stopCameraAnim,{capture:true});
    canvas.addEventListener("dblclick",stopCameraAnim);
  }
  renderTree();
}

export {initTree,renderTree};

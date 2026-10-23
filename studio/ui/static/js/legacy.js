import {api as apiRequest, onRevision, studioToken} from "./api.js";
import {
  briefRequest,
  defaultMapMode,
  plannedNodes,
  plannedStats,
  shortPrompt,
  statusText,
  verifyLabel,
} from "./briefmodel.js";
import {
  enableNodeDrag as enableNodeDragImpl,
  enableZoneDrag as enableZoneDragImpl,
} from "./drag.js";
import {EDITORS, buildEditorUrl} from "./editor.js";
import {
  clearStage,
  drawEdges as drawEdgesImpl,
  measure,
  offsetInStage,
  sizeStage,
} from "./edges.js";
import {
  areaSize as geometryAreaSize,
  inferEdgeKind,
  methodOrder,
  proposedOverlap as geometryOverlap,
  proposedZoneRect,
  zoneOverlaps as geometryZoneOverlaps,
} from "./geometry.js";
import {matchesAll, searchNodes} from "./search.js";
import {cleanPositions, createStorage, storageKey} from "./storage.js";
import {nextState, saveLabel} from "./savestate.js";
import {$, esc, toast} from "./util.js";
import {
  applyTransform as applyTransformImpl,
  bindViewport,
  fitView,
  revealElement,
} from "./viewport.js";

"use strict";
const KIND_NAMES={endpoint:"эндпоинт",scenario:"сценарий",io:"io",model:"модель",test:"тест"};
const ROLE_NAMES={schema:"схема",dependency:"зависимость",exception:"исключение",settings:"настройки",utils:"утилита",other:"прочее",method:"метод",function:"функция"};
const KIND_COLORS={endpoint:"#5aa0ff",scenario:"#e8833a",io:"#7fc97f",model:"#bb9af7",test:"#c9a86a"};
const EDGE_COLORS={dispatch:"#5aa0ff",scenario_call:"#e8833a",port_call:"#7fc97f",table:"#bb9af7",tests:"#c9a86a",uses:"#9a9aa2",depends:"#9a9aa2"};
const EDGE_NAMES={dispatch:"вызов сценария",scenario_call:"вызов сценария",port_call:"порт БД",table:"таблица",tests:"тест",uses:"использует",depends:"зависит"};
const METHOD_COLORS={GET:"#5aa0ff",POST:"#7fc97f",PUT:"#e8833a",PATCH:"#bb9af7",DELETE:"#ff5d5d","ДРУГОЙ":"#9a9aa2","БЕЗ МЕТОДА":"#9a9aa2"};
const NO_DOMAIN="без домена";
const NO_METHOD="без метода";
const CARD_W=280,GAPY=24,PAD=30;
const CREATE_KINDS=["endpoint","scenario","io","model","test"];
const CREATE_LAYER_X={endpoint:40,scenario:400,io:760,model:1120,test:1480};

let G=null,byId={},tab="map",selectedScenario=null,selectedNode=null,hideMeta=false;
let scenView={x:30,y:30,k:1},mapView={x:30,y:30,k:1},createView={x:30,y:30,k:1};
let scenBoxes={},mapBoxes={};
const mapPos=new Map(),scenPos=new Map(),periphPos=new Map(),listOpen=new Map();
let scenPeripheralEdges=[];
let incompleteZoneMoved=false;
const mapFastbase={x:40,y:40},mapMethods=new Map(),scenZonePos=new Map();
let justDraggedAt=0;
const createState={nodes:[],edges:[],seq:1,zones:{}};
let createConnect=null;
let studioRevision=0,liveFailures=0,liveStarted=false,dragging=false;
const domainFilter=new Set();
onRevision(value=>{studioRevision=Math.max(studioRevision,value)});
const api=apiRequest;
let uiStorage=null,saveState="idle",formDirty=false,persistTimer=null,lastSearchQuery="";
let editorTemplate="",editorId="vscode",studioInfo=null;
let briefReport=null,briefLast=null,briefStatusTimer=null,briefPreview=null;
try{editorTemplate=localStorage.getItem("studio:editor-template")||"";editorId=localStorage.getItem("studio:editor")||"vscode"}catch(_){ }


function applyGraph(g){G=g||{};byId={};(G.nodes||[]).forEach(n=>byId[n.id]=n);
  if(selectedScenario&&!G.scenario_paths?.[selectedScenario])selectedScenario=null;
  if(selectedNode&&!byId[selectedNode])selectedNode=null;
  updateBriefBadge();
  scheduleBriefStatus();
}
function methodOfEndpoint(n){const m=String((n.details||{}).http_method||"").toUpperCase();return m||"БЕЗ МЕТОДА"}
function urlOfEndpoint(n){const d=n.details||{};return d.url_path||d.path||""}
function endpointLabel(n){
  const d=n.details||{};
  return `${methodOfEndpoint(n)} ${urlOfEndpoint(n)}`.trim()+"\n"+(d.function_name||"");
}
function nodeMeta(n){
  const d=n.details||{};
  if(n.kind==="endpoint")return d.function_name||"";
  if(n.kind==="io")return `_db.${d.method||""}${d.table?"(\u0022"+d.table+"\u0022)":""}`;
  if(n.kind==="model")return `таблица ${d.tablename||""}`;
  if(n.kind==="test")return d.module||"";
  return d.class_name||d.name||"";
}
function cardHtml(n,opts={}){
  const c=opts.method?METHOD_COLORS[opts.method]:KIND_COLORS[n.kind]||"#888";
  const extra=(n.details&&n.details.extra_routes||[]).length;
  let h=`<div class="hd"><span class="grip" title="перетащить">⠿</span><span class="chip">${esc(KIND_NAMES[n.kind]||n.kind)}</span>${n.planned?'<span class="chip plan">без кода</span>':""}${extra?`<span class="chip" title="Ещё маршруты: ${extra}">+${extra}</span>`:""}<span class="t">${esc(n.label)}</span><button class="open-editor" type="button" title="Открыть в редакторе" aria-label="Открыть в редакторе">↗</button></div>`;
  h+=`<div class="mt">${esc(nodeMeta(n))}</div>`;
  if(!hideMeta){
    if(n.description){
      h+=`<div class="sec"><div class="lbl">описание</div><div class="body">${esc(n.description)}</div></div>`;
    }
    if(n.acceptance&&n.acceptance.length){
      h+=`<div class="sec"><div class="lbl">критерии приёмки</div><div class="body">${esc(n.acceptance.join("\n"))}</div></div>`;
    }
    if(n.domain){
      h+=`<div class="sec"><div class="lbl">${n.kind==="endpoint"?"домен":"домен (наследуется)"}</div><div class="body">${esc(n.domain)}</div></div>`;
    }
  }
  const cls=`card k-${n.kind}${n.planned?" planned":""}${opts.method?" method-"+opts.method.toLowerCase():""}${opts.nested?" nested":""}${selectedNode===n.id?" sel":""}`;
  return {cls,html:h,c};
}
function periphLayoutKey(scope,pid){return `${scope}|${pid}`}
function makeCard(n,x,y,opts={},parent=null,scope=selectedScenario){
  const key=`${scope}|${n.id}`;
  const saved=scenPos.get(key);
  if(saved){x=saved.x;y=saved.y}
  const spec=cardHtml(n,opts);
  const e=document.createElement("div");
  e.className=spec.cls;e.dataset.id=n.id;e.style.left=x+"px";e.style.top=y+"px";e.title=n.id;
  e.innerHTML=spec.html;
  e.tabIndex=0;e.setAttribute("role","button");e.setAttribute("aria-label",n.label);
  e.onclick=ev=>{ev.stopPropagation();onCardClick(n.id)};
  e.addEventListener("keydown",ev=>{
    if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();ev.stopPropagation();onCardClick(n.id)}
    if(ev.key.toLowerCase()==="o"){ev.preventDefault();openEditorForNode(n)}
  });
  const openBtn=e.querySelector(".open-editor");
  if(openBtn)openBtn.onclick=ev=>{ev.stopPropagation();openEditorForNode(n)};
  (parent||$("scenStage")).appendChild(e);
  enableNodeDrag(e,"scen",key,x,y,scenView,
    (nx,ny)=>scenMoveNode(e,key,scope,nx,ny),
    ()=>{recomputeScenarioSizes();redrawScenarioEdges()});
  return e;
}
function scenMoveNode(card,key,scope,nx,ny){
  const zone=card.parentElement;if(!zone)return;
  const cx=Math.max(14,nx),cy=Math.max(34,ny);
  const oldX=parseFloat(card.style.left)||0,oldY=parseFloat(card.style.top)||0;
  const dx=cx-oldX,dy=cy-oldY;
  const moving=[card],proposed=[{el:card,x:cx,y:cy}];
  const periphs=[...zone.querySelectorAll(".periph-node")].filter(pc=>pc.dataset.parentId===card.dataset.id);
  for(const pc of periphs){
    const px=(parseFloat(pc.style.left)||0)+dx,py=(parseFloat(pc.style.top)||0)+dy;
    if(px<14||py<34)return;
    moving.push(pc);proposed.push({el:pc,x:px,y:py});
  }
  if(proposedOverlap(zone,moving,proposed))return;
  const overrides=new Map(proposed.map(p=>[p.el,{x:p.x,y:p.y}]));
  if(zoneOverlaps(zone,proposedZoneRect(zone,areaSize(zone,overrides)),scenZones()))return;
  card.style.left=cx+"px";card.style.top=cy+"px";
  scenPos.set(key,{x:cx,y:cy});
  periphs.forEach((pc,index)=>{
    const p=proposed[index+1];
    pc.style.left=p.x+"px";pc.style.top=p.y+"px";
    periphPos.set(periphLayoutKey(scope,pc.dataset.id),{x:p.x,y:p.y});
  });
  recomputeScenarioSizes();redrawScenarioEdges();
}
function scenMovePeriph(card,key,scope,nx,ny){
  const zone=card.parentElement;if(!zone)return;
  const cx=Math.max(14,nx),cy=Math.max(34,ny);
  if(proposedOverlap(zone,[card],[{el:card,x:cx,y:cy}]))return;
  if(zoneOverlaps(zone,proposedZoneRect(zone,areaSize(zone,new Map([[card,{x:cx,y:cy}]]))),scenZones()))return;
  card.style.left=cx+"px";card.style.top=cy+"px";
  periphPos.set(periphLayoutKey(scope,card.dataset.id),{x:cx,y:cy});
  recomputeScenarioSizes();redrawScenarioEdges();
}
function scenZones(){return[...$("scenStage").querySelectorAll(".scen-zone")]}
function incompleteNodes(){
  return (G.nodes||[]).filter(n=>n.complete===false);
}
async function onCardClick(id){
  if(Date.now()-justDraggedAt<250)return;
  const n=byId[id];if(!n)return;
  await flushPending();
  if(tab==="map"&&n.kind==="endpoint"){openEndpointScenario(id);return}
  selectedNode=id;renderScenario();renderInspector();schedulePersist();
}
async function openEndpointScenario(endpointId){
  await flushPending();
  const edge=(G.edges||[]).find(e=>e.kind==="dispatch"&&e.from_node===endpointId);
  if(!edge){toast("У эндпоинта нет сценария (dispatch не найден)");return}
  selectedScenario=edge.to_node;selectedNode=endpointId;setTab("scenario");
}

/* ---------- вкладки ---------- */
async function setTab(name){
  await flushPending();
  tab=name;
  document.querySelectorAll(".tabbtn").forEach(b=>b.classList.toggle("on",b.dataset.tab===name));
  document.querySelectorAll(".tab").forEach(s=>s.classList.toggle("on",s.id==="tab-"+name));
  if(name==="map")renderMap();
  else if(name==="scenario")renderScenario();
  else renderCreate();
  schedulePersist();
}
document.querySelectorAll(".tabbtn").forEach(b=>b.onclick=()=>setTab(b.dataset.tab));

/* ---------- viewport ---------- */
function applyTransform(viewId,view){
  applyTransformImpl(viewId,view,updateCreateZoneScales);
}
function updateCreateZoneScales(){
  const stage=$("createStage");if(!stage)return;
  const k=createView.k||1;
  const baseF=Math.max(.75,Math.min(1.6,k))/k;
  stage.querySelectorAll(".create-zone").forEach(zone=>{
    const lab=zone.querySelector(".zlab");if(!lab)return;
    const maxF=Math.max(.35,(zone.offsetWidth-18)/Math.max(1,lab.offsetWidth));
    const f=Math.min(baseF,maxF);
    lab.style.transformOrigin="left center";
    lab.style.transform=`scale(${f})`;
  });
}

/* ---------- перемещение узлов ---------- */
const dragHooks={
  start:()=>{dragging=true},
  end:moved=>{dragging=false;if(moved)justDraggedAt=Date.now()},
  persist:schedulePersist,
};
function enableZoneDrag(zone,view,onMove,onEnd){
  enableZoneDragImpl(zone,view,onMove,onEnd,dragHooks);
}
function enableNodeDrag(card,scope,key,x,y,view,onMove,onEnd){
  enableNodeDragImpl(card,view,onMove,onEnd,dragHooks);
}
function drawEdges(svg,boxes,edges){
  drawEdgesImpl(svg,boxes,edges,EDGE_COLORS,EDGE_NAMES,esc);
}
function areaSize(zone,overrides){
  const rects=[...zone.querySelectorAll(".card[data-id],.create-card[data-cid]")].map(el=>{
    const ov=overrides&&overrides.get(el);
    return{x:ov?ov.x:(parseFloat(el.style.left)||0),y:ov?ov.y:(parseFloat(el.style.top)||0),w:el.offsetWidth,h:el.offsetHeight};
  });
  return geometryAreaSize(rects);
}
function proposedOverlap(zone,movingEls,proposed){
  const moving=new Set(movingEls);
  const others=[...zone.querySelectorAll(".card[data-id],.create-card[data-cid]")].filter(el=>!moving.has(el)).map(styleRect);
  const rects=proposed.map(p=>({x:p.x,y:p.y,w:p.el.offsetWidth,h:p.el.offsetHeight}));
  return geometryOverlap(rects,others,6);
}
function zoneOverlaps(zone,rect,zones){
  return geometryZoneOverlaps(rect,zones.filter(other=>other!==zone).map(styleRect),10);
}

/* ---------- Карта ---------- */
function styleRect(el){
  return{x:parseFloat(el.style.left)||0,y:parseFloat(el.style.top)||0,w:el.offsetWidth,h:el.offsetHeight};
}
function mapMethodZones(){return[...$("mapStage").querySelectorAll(".map-method")]}
function recomputeMapSizes(){
  const stage=$("mapStage"),svg=$("mapEdges"),fast=stage.querySelector(".map-fastbase");
  if(!fast)return;
  fast.querySelectorAll(".map-method").forEach(zone=>{
    let w=240,h=70;
    zone.querySelectorAll(".card[data-id]").forEach(card=>{
      const r=styleRect(card);
      w=Math.max(w,r.x+r.w+14);h=Math.max(h,r.y+r.h+14);
    });
    zone.style.width=w+"px";zone.style.height=h+"px";
  });
  let fw=280,fh=90;
  fast.querySelectorAll(".map-method").forEach(zone=>{
    const r=styleRect(zone);
    fw=Math.max(fw,r.x+r.w+18);fh=Math.max(fh,r.y+r.h+18);
  });
  fast.style.width=fw+"px";fast.style.height=fh+"px";
  sizeStage(stage,svg);
}
function renderMap(){
  const stage=$("mapStage"),svg=$("mapEdges");
  clearStage(stage,svg);mapBoxes={};
  renderDomainChips();
  const allEndpoints=(G.nodes||[]).filter(n=>n.kind==="endpoint");
  const endpoints=allEndpoints.filter(n=>{
    if(lastSearchQuery&&!matchesAll(lastSearchQuery,[n.label,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description,(n.details||{}).function_name]))return false;
    if(!domainFilter.size)return true;
    const names=String(n.domain||NO_DOMAIN).split(",").map(s=>s.trim());
    return names.some(name=>domainFilter.has(name));
  });
  if(!endpoints.length){
    const e=document.createElement("div");e.className="empty";
    if(allEndpoints.length){
      e.innerHTML=`Ничего не найдено по запросу «${esc(lastSearchQuery)}».<br>Очистите поле поиска.`;
    }else{
      const env=(studioInfo&&studioInfo.env)||{};
      e.innerHTML=`Эндпоинтов fastbase не найдено.<br>`+
        `<span class="hint">Проверено: ${esc(env.routes_package||"app.api.v1.routes")} · `+
        `сценарии: ${esc(env.core_discover||"app.scenarios")} · модели: ${esc(env.models||"models.py")}</span><br>`+
        `Поправьте FAT_ROUTES_PACKAGE в .env.fastbase или выполните <b>studio doctor</b>.`;
    }
    stage.appendChild(e);stage.style.width="900px";stage.style.height="500px";applyTransform("mapView",mapView);return;
  }
  const byMethod=new Map();
  endpoints.forEach(n=>{
    const method=methodOfEndpoint(n);
    if(!byMethod.has(method))byMethod.set(method,[]);
    byMethod.get(method).push(n);
  });
  const methods=[...byMethod.keys()].sort((a,b)=>methodOrder(a)-methodOrder(b)||a.localeCompare(b));
  const fast=document.createElement("div");
  fast.className="zone map-zone map-fastbase";
  fast.style.left=mapFastbase.x+"px";fast.style.top=mapFastbase.y+"px";
  fast.style.width="320px";fast.style.height="120px";
  const fastLab=document.createElement("div");fastLab.className="zlab";
  fastLab.innerHTML='<span class="grip" title="перетащить">⠿</span><b style="color:var(--acc)">fastbase</b>';fast.appendChild(fastLab);
  stage.appendChild(fast);

  const zones={};
  methods.forEach(method=>{
    const items=byMethod.get(method).slice().sort((a,b)=>(a.domain||"").localeCompare(b.domain||"")||a.label.localeCompare(b.label));
    const zone=document.createElement("div");
    zone.className="zone map-zone map-method";zone.dataset.method=method;
    zone.style.left="0px";zone.style.top="38px";zone.style.width="240px";zone.style.height="70px";
    const lab=document.createElement("div");lab.className="zlab";
    lab.innerHTML=`<span class="grip" title="перетащить">⠿</span><span class="method-dot" style="background:${METHOD_COLORS[method]||"#9a9aa2"}"></span>${esc(method)} <span class="hint">· ${items.length}</span>`;
    zone.appendChild(lab);fast.appendChild(zone);zones[method]=zone;
    items.forEach((n,i)=>{
      const d=n.details||{};
      const card=document.createElement("div");
      card.className=`card method-${method.toLowerCase()}${n.planned?" planned":""}`;card.dataset.id=n.id;
      const saved=mapPos.get(n.id);
      const lx=saved?saved.x:14+(i%2)*292;
      const ly=saved?saved.y:38+Math.floor(i/2)*94;
      card.style.left=lx+"px";card.style.top=ly+"px";
      const url=urlOfEndpoint(n);
      const title=url?`${method} ${url}`:`${method} · ${n.label}`;
      const sub=url?(d.function_name||""):"URL неизвестен";
      card.innerHTML=`<div class="hd"><span class="grip" title="перетащить">⠿</span>${n.planned?'<span class="chip plan">без кода</span>':""}<span class="t">${esc(title)}</span><button class="open-editor" type="button" title="Открыть в редакторе" aria-label="Открыть в редакторе">↗</button></div>`+
        `<div class="mt">${esc(sub)}</div>`;
      card.tabIndex=0;card.setAttribute("role","button");card.setAttribute("aria-label",title);
      card.onclick=ev=>{ev.stopPropagation();if(Date.now()-justDraggedAt<250)return;openEndpointScenario(n.id)};
      card.addEventListener("keydown",ev=>{if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();openEndpointScenario(n.id)}});
      const mapOpen=card.querySelector(".open-editor");
      if(mapOpen)mapOpen.onclick=ev=>{ev.stopPropagation();openEditorForNode(n)};
      zone.appendChild(card);
      enableNodeDrag(card,"map",n.id,lx,ly,mapView,
        (nx,ny)=>{
          const cx=Math.max(14,nx),cy=Math.max(38,ny);
          const proposed=[{el:card,x:cx,y:cy}];
          if(proposedOverlap(zone,[card],proposed))return;
          const size=areaSize(zone,new Map([[card,{x:cx,y:cy}]]));
          if(zoneOverlaps(zone,proposedZoneRect(zone,size),mapMethodZones()))return;
          card.style.left=cx+"px";card.style.top=cy+"px";
          mapPos.set(n.id,{x:cx,y:cy});recomputeMapSizes();
        },
        ()=>{
          mapPos.set(n.id,{x:parseFloat(card.style.left)||0,y:parseFloat(card.style.top)||0});
          recomputeMapSizes();
        });
    });
  });
  recomputeMapSizes();
  let cursor=18;
  methods.forEach(method=>{
    const zone=zones[method];
    let pos=mapMethods.get(method);
    if(!pos){pos={x:cursor,y:38};mapMethods.set(method,pos);cursor+=zone.offsetWidth+28}
    zone.style.left=pos.x+"px";zone.style.top=pos.y+"px";
  });
  recomputeMapSizes();
  methods.forEach(method=>{
    const zone=zones[method];
    enableZoneDrag(zone,mapView,
      (nx,ny)=>{
        const cx=Math.max(16,nx),cy=Math.max(38,ny);
        const rect={x:cx,y:cy,w:zone.offsetWidth,h:zone.offsetHeight};
        if(zoneOverlaps(zone,rect,mapMethodZones()))return;
        zone.style.left=cx+"px";zone.style.top=cy+"px";
        mapMethods.set(method,{x:cx,y:cy});recomputeMapSizes();
      },
      ()=>{
        mapMethods.set(method,{x:parseFloat(zone.style.left)||0,y:parseFloat(zone.style.top)||0});
        recomputeMapSizes();
      });
  });
  enableZoneDrag(fast,mapView,
    (nx,ny)=>{
      const cx=Math.max(20,nx),cy=Math.max(20,ny);
      fast.style.left=cx+"px";fast.style.top=cy+"px";
      mapFastbase.x=cx;mapFastbase.y=cy;
    },
    ()=>{mapFastbase.x=parseFloat(fast.style.left)||40;mapFastbase.y=parseFloat(fast.style.top)||40;sizeStage(stage,svg)});
  sizeStage(stage,svg);
  applyTransform("mapView",mapView);
}

/* ---------- Сценарий ---------- */

function endpointsOfScenario(id){
  const path=G.scenario_paths&&G.scenario_paths[id];
  const ids=(path&&path.endpoints&&path.endpoints.length)?path.endpoints:[];
  if(ids.length)return ids.filter(ep=>byId[ep]);
  return (G.edges||[]).filter(e=>e.kind==="dispatch"&&e.to_node===id&&byId[e.from_node]).map(e=>e.from_node);
}
function methodsOfScenario(id){
  const methods=new Set();
  endpointsOfScenario(id).forEach(epId=>{
    const ep=byId[epId];
    if(ep&&ep.kind==="endpoint")methods.add(methodOfEndpoint(ep));
  });
  if(!methods.size)methods.add(NO_METHOD);
  return [...methods].sort((a,b)=>methodOrder(a)-methodOrder(b)||a.localeCompare(b));
}
function domainsOfScenario(n){
  const parts=String(n.domain||"").split(",").map(s=>s.trim()).filter(Boolean);
  return parts.length?parts:[NO_DOMAIN];
}
function domainOrder(a,b){
  if(a===NO_DOMAIN)return 1;
  if(b===NO_DOMAIN)return -1;
  return a.localeCompare(b);
}
function fillScenarioSelect(){
  const sel=$("scenSelect");sel.innerHTML="";
  const scenarios=(G.scenarios||[]).filter(id=>{
    const n=byId[id];if(!n)return false;
    return !lastSearchQuery||matchesAll(lastSearchQuery,[n.label,n.domain,n.description,n.file_path]);
  });
  scenarios.forEach(id=>{const n=byId[id];if(!n)return;
    const o=document.createElement("option");o.value=id;o.textContent=n.label;o.selected=id===selectedScenario;sel.appendChild(o)});
  sel.onchange=()=>{selectedScenario=sel.value;selectedNode=null;renderScenario();renderInspector()};
  const list=$("scenList");
  list.querySelectorAll("details[data-key]").forEach(d=>listOpen.set(d.dataset.key,d.open));
  list.innerHTML="";
  if(!scenarios.length){
    const env=(studioInfo&&studioInfo.env)||{};
    list.innerHTML=lastSearchQuery
      ?'<p class="hint" style="padding:6px">Ничего не найдено.</p>'
      :`<p class="hint" style="padding:6px">Сценариев нет.<br>Проверено: ${esc(env.core_discover||"app.scenarios")}<br>`+
        `Поправьте FAT_CORE_DISCOVER или выполните <b>studio doctor</b>.</p>`;
    return;
  }

  const groups=new Map();
  scenarios.forEach(id=>{
    const n=byId[id];if(!n)return;
    methodsOfScenario(id).forEach(method=>{
      if(!groups.has(method))groups.set(method,new Map());
      const byDomain=groups.get(method);
      domainsOfScenario(n).forEach(domain=>{
        if(!byDomain.has(domain))byDomain.set(domain,[]);
        const items=byDomain.get(domain);
        if(!items.includes(id))items.push(id);
      });
    });
  });

  const methods=[...groups.keys()].sort((a,b)=>methodOrder(a)-methodOrder(b)||a.localeCompare(b));
  methods.forEach(method=>{
    const byDomain=groups.get(method);
    let total=0;byDomain.forEach(items=>total+=items.length);
    const mDet=document.createElement("details");
    mDet.dataset.key="m:"+method;mDet.dataset.level="method";
    mDet.open=listOpen.has(mDet.dataset.key)?listOpen.get(mDet.dataset.key):true;
    const mSum=document.createElement("summary");
    mSum.innerHTML=`<span class="method-dot" style="background:${METHOD_COLORS[method]||"#9a9aa2"}"></span>${esc(method)} <span class="hint">· ${total}</span>`;
    mDet.appendChild(mSum);
    [...byDomain.keys()].sort(domainOrder).forEach(domain=>{
      const items=byDomain.get(domain);
      const dDet=document.createElement("details");
      dDet.dataset.key="d:"+method+":"+domain;dDet.dataset.level="domain";
      dDet.open=listOpen.has(dDet.dataset.key)?listOpen.get(dDet.dataset.key):true;
      const dSum=document.createElement("summary");
      dSum.innerHTML=`${esc(domain)} <span class="hint">· ${items.length}</span>`;
      dDet.appendChild(dSum);
      items.forEach(id=>{
        const n=byId[id];if(!n)return;
        const e=document.createElement("div");
        e.className="sitem"+(selectedScenario===id?" on":"");e.textContent=n.label;e.title=n.id;
        e.onclick=()=>{selectedScenario=id;selectedNode=null;renderScenario();renderInspector()};
        dDet.appendChild(e);
      });
      mDet.appendChild(dDet);
    });
    list.appendChild(mDet);
  });
  const incomplete=incompleteNodes();
  const incDet=document.createElement("details");
  incDet.dataset.key="incomplete";incDet.dataset.level="method";
  incDet.open=listOpen.has("incomplete")?listOpen.get("incomplete"):true;
  const incSum=document.createElement("summary");
  incSum.innerHTML=`Неполные <span class="hint">· ${incomplete.length}</span>`;
  incDet.appendChild(incSum);
  if(!incomplete.length){
    const empty=document.createElement("div");empty.className="hint";empty.style.padding="3px 8px 4px 14px";empty.textContent="(нет)";
    incDet.appendChild(empty);
  }
  incomplete.slice().sort((a,b)=>KIND_NAMES[a.kind].localeCompare(KIND_NAMES[b.kind])||a.label.localeCompare(b.label)).forEach(n=>{
    const e=document.createElement("div");
    e.className="sitem";e.title=n.id;
    e.innerHTML=`<span class="hint">${esc(KIND_NAMES[n.kind]||n.kind)}</span> · ${esc(n.label)}`;
    e.onclick=()=>focusIncompleteNode(n.id);
    incDet.appendChild(e);
  });
  list.appendChild(incDet);
}
function focusIncompleteNode(id){
  selectedNode=id;renderScenario();renderInspector();
  const el=[...document.querySelectorAll("#scenStage .card[data-id]")].find(e=>e.dataset.id===id);
  if(el)revealElement($("scenCanvas"),scenView,el,()=>applyTransform("scenView",scenView));
}
function scenarioEdges(ids){
  const set=new Set(ids);
  const base=(G.edges||[]).filter(e=>set.has(e.from_node)&&set.has(e.to_node));
  return base.concat(scenPeripheralEdges.filter(e=>set.has(e.from_node)&&set.has(e.to_node)));
}
function redrawScenarioEdges(){
  const stage=$("scenStage"),svg=$("scenEdges");
  scenBoxes=measure(stage);
  drawEdges(svg,scenBoxes,scenarioEdges(Object.keys(scenBoxes)));
}
function recomputeScenarioSizes(){
  const stage=$("scenStage"),svg=$("scenEdges");
  stage.querySelectorAll(".scen-zone").forEach(zone=>{
    const size=areaSize(zone);
    zone.style.width=size.w+"px";zone.style.height=size.h+"px";
  });
  sizeStage(stage,svg);
}
function renderPeripherals(zone,parentCard,node,scope){
  let cursor=(parseFloat(parentCard.style.top)||0)+parentCard.offsetHeight+10;
  const left=(parseFloat(parentCard.style.left)||0)+18;
  (node.peripherals||[]).forEach((item,index)=>{
    const pid=`periph:${node.id}:${index}`;
    const key=periphLayoutKey(scope,pid);
    const saved=periphPos.get(key);
    const x=saved?saved.x:left;
    const y=saved?saved.y:cursor;
    const pc=document.createElement("div");
    pc.className="card periph-node";pc.dataset.id=pid;pc.dataset.parentId=node.id;
    pc.style.left=x+"px";pc.style.top=y+"px";pc.title=item.name||"";
    const symbols=(item.symbols||[]).join(", ");
    pc.innerHTML=`<div class="hd"><span class="grip" title="перетащить">⠿</span><span class="chip">периферийный</span><span class="t">${esc(symbols||item.name||"")}</span></div>`+
      `<div class="mt">${esc(ROLE_NAMES[item.role]||item.role||"")} · ${esc(item.name||"")}</div>`;
    pc.onclick=ev=>{ev.stopPropagation();if(Date.now()-justDraggedAt<250)return;selectedNode=node.id;renderScenario();renderInspector()};
    zone.appendChild(pc);
    enableNodeDrag(pc,"scen",key,x,y,scenView,
      (nx,ny)=>scenMovePeriph(pc,key,scope,nx,ny),
      ()=>{recomputeScenarioSizes();redrawScenarioEdges()});
    scenPeripheralEdges.push({
      from_node:node.id,
      to_node:pid,
      kind:item.role==="dependency"?"depends":"uses",
      label:ROLE_NAMES[item.role]||item.role||"",
    });
    cursor=Math.max(cursor,y+pc.offsetHeight+8);
  });
}
function renderScenario(){
  fillScenarioSelect();
  const stage=$("scenStage"),svg=$("scenEdges");
  clearStage(stage,svg);scenBoxes={};scenPeripheralEdges=[];
  if(!G.scenarios.length){
    const e=document.createElement("div");e.className="empty";e.innerHTML="Сценариев не найдено.<br>Запустите скан проекта.";stage.appendChild(e);
    stage.style.width="900px";stage.style.height="500px";applyTransform("scenView",scenView);renderInspector();return;
  }
  if(!selectedScenario)selectedScenario=G.scenarios[0];
  const path=G.scenario_paths[selectedScenario];
  if(!path){const e=document.createElement("div");e.className="empty";e.textContent="У сценария нет пути.";stage.appendChild(e);applyTransform("scenView",scenView);return}
  const defs=[["endpoint","эндпоинт",30],["scenario","сценарий",380],["io","io",730],["model","модель",1080],["tests","тесты",1430],["incomplete","неполные",30]];
  const layers={};
  defs.forEach(([kind,title,defaultX])=>{
    const zone=document.createElement("div");
    zone.className="zone scen-zone";zone.dataset.layer=kind;
    const key=kind==="incomplete"?"incomplete|zone":`${selectedScenario}|zone|${kind}`;
    let pos=scenZonePos.get(key);
    if(!pos){pos={x:defaultX,y:kind==="incomplete"?620:40};scenZonePos.set(key,pos)}
    zone.style.left=pos.x+"px";zone.style.top=pos.y+"px";
    zone.style.width="240px";zone.style.height="70px";
    const lab=document.createElement("div");lab.className="zlab";
    lab.innerHTML=`<span class="grip" title="перетащить">⠿</span>${esc(title)} <span class="hint">· 0</span>`;
    zone.appendChild(lab);stage.appendChild(zone);layers[kind]=zone;
    enableZoneDrag(zone,scenView,
      (nx,ny)=>{
        const cx=Math.max(10,nx),cy=Math.max(10,ny);
        const rect={x:cx,y:cy,w:zone.offsetWidth,h:zone.offsetHeight};
        if(zoneOverlaps(zone,rect,scenZones()))return;
        zone.style.left=cx+"px";zone.style.top=cy+"px";
        scenZonePos.set(key,{x:cx,y:cy});
        if(kind==="incomplete")incompleteZoneMoved=true;
        redrawScenarioEdges();
      },
      ()=>{
        scenZonePos.set(key,{x:parseFloat(zone.style.left)||0,y:parseFloat(zone.style.top)||0});
        recomputeScenarioSizes();redrawScenarioEdges();
      });
  });
  const nextY={};
  const add=(id,layer,x,y,opts,scope)=>{
    if(!id||!byId[id])return null;
    const card=makeCard(byId[id],x,y,opts,layers[layer],scope);
    renderPeripherals(layers[layer],card,byId[id],scope);
    return card;
  };
  const stack=(id,layer,opts)=>{
    const y=nextY[layer]===undefined?38:nextY[layer];
    const card=add(id,layer,14,y,opts,selectedScenario);
    if(card){
      let bottom=(parseFloat(card.style.top)||y)+card.offsetHeight;
      layers[layer].querySelectorAll(".periph-node").forEach(pc=>{
        if(pc.dataset.parentId===card.dataset.id)bottom=Math.max(bottom,(parseFloat(pc.style.top)||0)+pc.offsetHeight);
      });
      nextY[layer]=bottom+12;
    }
    return card;
  };
  if(path.endpoint){
    stack(path.endpoint,"endpoint",{});
    stack(path.scenario,"scenario",{});
    (path.called_scenarios||[]).forEach(id=>{if(id!==selectedScenario)stack(id,"scenario",{nested:true})});
    (path.io_calls||[]).forEach(id=>stack(id,"io",{}));
    (path.models||[]).forEach(id=>stack(id,"model",{}));
    (path.tests||[]).forEach(id=>stack(id,"tests",{}));
  }else{
    const note=document.createElement("div");note.className="hint";note.style.position="absolute";note.style.left="14px";note.style.top="42px";
    note.textContent="нет эндпоинта — смотрите область «неполные»";layers.endpoint.appendChild(note);
  }
  const columnX={scenario:14,io:330,model:646,test:962,endpoint:14};
  const columnY={};
  incompleteNodes()
    .slice()
    .sort((a,b)=>(KIND_NAMES[a.kind]||"").localeCompare(KIND_NAMES[b.kind]||"")||a.label.localeCompare(b.label))
    .forEach(n=>{
      const layer=layers.incomplete;
      const x=columnX[n.kind]!==undefined?columnX[n.kind]:14;
      const y=columnY[n.kind]===undefined?38:columnY[n.kind];
      const card=add(n.id,"incomplete",x,y,{}, "incomplete");
      let bottom=(parseFloat(card.style.top)||y)+card.offsetHeight;
      layer.querySelectorAll(".periph-node").forEach(pc=>{
        if(pc.dataset.parentId===card.dataset.id)bottom=Math.max(bottom,(parseFloat(pc.style.top)||0)+pc.offsetHeight);
      });
      columnY[n.kind]=bottom+12;
    });
  defs.forEach(([kind])=>{
    const zone=layers[kind];
    const count=zone.querySelectorAll(".card[data-id]").length;
    const hint=zone.querySelector(".zlab .hint");
    if(hint)hint.textContent="· "+count;
  });
  recomputeScenarioSizes();
  if(!incompleteZoneMoved){
    let bottom=40;
    defs.forEach(([kind])=>{
      if(kind==="incomplete")return;
      const zone=layers[kind];
      bottom=Math.max(bottom,(parseFloat(zone.style.top)||0)+zone.offsetHeight);
    });
    layers.incomplete.style.left="30px";
    layers.incomplete.style.top=(bottom+40)+"px";
    scenZonePos.set("incomplete|zone",{x:30,y:bottom+40});
    recomputeScenarioSizes();
  }
  redrawScenarioEdges();
  applyTransform("scenView",scenView);
  renderInspector();
}

/* ---------- редактор, live, localStorage, диагностика ---------- */
function nodeAbsPath(n){
  const root=String((G&&G.project_root)||"").replace(/[\\/]+$/,"");
  return root?root+"/"+n.file_path:n.file_path;
}
function currentEditor(){return EDITORS.some(e=>e.id===editorId)?editorId:"vscode"}
function openEditorForNode(n){
  if(!n)return;
  const line=n.planned?1:(n.line||1);
  const url=buildEditorUrl(currentEditor(),nodeAbsPath(n),n.file_path,line,editorTemplate);
  const anchor=document.createElement("a");
  anchor.href=url;anchor.style.display="none";document.body.appendChild(anchor);anchor.click();anchor.remove();
  if(currentEditor()==="custom"&&!editorTemplate)toast("Задайте шаблон редактора: {path}, {rel}, {line}");
}
function copyNodeLocation(n){
  if(!n)return;
  const text=`${n.file_path}:${n.planned?1:n.line}`;
  const fallback=()=>{
    const area=document.createElement("textarea");
    area.value=text;area.style.position="fixed";area.style.opacity="0";
    document.body.appendChild(area);area.select();
    try{document.execCommand("copy")}catch(_){ }
    area.remove();
  };
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(()=>toast("Скопировано: "+text)).catch(()=>{fallback();toast("Скопировано: "+text)});
  }else{fallback();toast("Скопировано: "+text)}
}
function initEditorChoice(){
  const select=$("editorChoice");if(!select)return;
  select.innerHTML=EDITORS.map(e=>`<option value="${e.id}">${esc(e.title)}</option>`).join("");
  select.value=currentEditor();
  select.onchange=()=>{
    editorId=select.value;
    if(editorId==="custom"){
      const value=prompt("Шаблон ссылки на редактор: {path}, {rel}, {line}",editorTemplate||"{path}:{line}");
      if(value!==null){editorTemplate=value;try{localStorage.setItem("studio:editor-template",editorTemplate)}catch(_){ }}
    }
    try{localStorage.setItem("studio:editor",editorId)}catch(_){ }
    schedulePersist();
  };
}
function setSaveState(next){
  saveState=nextState(saveState,next);
  const foot=$("inspFoot");
  if(foot&&saveState!=="idle")foot.textContent=saveLabel(saveState);
}
function currentPositions(){
  const dump=map=>Object.fromEntries([...map.entries()]);
  return{map:dump(mapPos),scen:dump(scenPos),periph:dump(periphPos),zones:dump(scenZonePos)};
}
function uiSnapshot(){
  return{
    tab,selectedScenario,selectedNode,hideMeta,editor:editorId,editorTemplate,
    views:{map:mapView,scen:scenView,create:createView},
    positions:currentPositions(),
    listOpen:Object.fromEntries(listOpen),
    createDraft:{nodes:createState.nodes,edges:createState.edges,seq:createState.seq},
  };
}
function persistNow(){
  if(!uiStorage||!G)return;
  uiStorage.save(uiSnapshot());
}
function schedulePersist(){
  clearTimeout(persistTimer);
  persistTimer=setTimeout(persistNow,300);
}
function restoreUiState(saved){
  if(!saved)return;
  if(["map","scenario","create"].includes(saved.tab))tab=saved.tab;
  if(byId[saved.selectedNode])selectedNode=saved.selectedNode;
  if((G.scenario_paths||{})[saved.selectedScenario])selectedScenario=saved.selectedScenario;
  hideMeta=!!saved.hideMeta;
  if(EDITORS.some(e=>e.id===saved.editor))editorId=saved.editor;
  if(typeof saved.editorTemplate==="string")editorTemplate=saved.editorTemplate;
  for(const key of ["map","scen","create"]){
    const view=saved.views&&saved.views[key];
    if(view&&Number.isFinite(view.x)&&Number.isFinite(view.y)&&Number.isFinite(view.k)){
      const target=key==="map"?mapView:key==="scen"?scenView:createView;
      target.x=view.x;target.y=view.y;target.k=view.k;
    }
  }
  const ids=new Set(Object.keys(byId));
  const scopes=new Set(["incomplete",...(G.scenarios||[])]);
  const restored=pos=>{
    const out=new Map();
    for(const [key,value] of Object.entries(pos||{})){
      if(!value||!Number.isFinite(value.x)||!Number.isFinite(value.y))continue;
      const parts=key.split("|");
      const id=parts[parts.length-1];
      const scope=parts.slice(0,-1).join("|");
      if(!ids.has(id)&&!scopes.has(scope)&&!scope.includes("incomplete"))continue;
      out.set(key,{x:value.x,y:value.y});
    }
    return out;
  };
  const positions=(saved.positions||{});
  restored(positions.map).forEach((v,k)=>mapPos.set(k,v));
  restored(positions.scen).forEach((v,k)=>scenPos.set(k,v));
  restored(positions.periph).forEach((v,k)=>periphPos.set(k,v));
  Object.entries(positions.zones||{}).forEach(([k,v])=>{
    if(v&&Number.isFinite(v.x)&&Number.isFinite(v.y))scenZonePos.set(k,{x:v.x,y:v.y});
  });
  Object.entries(saved.listOpen||{}).forEach(([key,value])=>listOpen.set(key,!!value));
  const draft=saved.createDraft;
  if(draft&&Array.isArray(draft.nodes)&&draft.nodes.length){
    createState.nodes=draft.nodes;createState.edges=Array.isArray(draft.edges)?draft.edges:[];createState.seq=draft.seq||1;
    toast(`Восстановлен черновик (${draft.nodes.length} узлов)`);
  }
}
function initUiStorage(){
  try{
    uiStorage=createStorage(window.localStorage,storageKey((G&&G.project_root)||location.origin));
    restoreUiState(uiStorage.load());
    if($("hideMeta"))$("hideMeta").checked=hideMeta;
  }catch(_){uiStorage=null}
}
function setLive(online){
  const dot=$("liveDot");if(!dot)return;
  dot.classList.toggle("on",online);
  dot.classList.toggle("off",!online);
  dot.title=online?"live · обновлено "+new Date().toLocaleTimeString(): "нет связи со студией — перезапустите studio";
  dot.textContent=online?"● live":"● нет связи";
}
function rerenderActive(){
  if(tab==="map")renderMap();
  else if(tab==="scenario")renderScenario();
  else renderCreate();
  updateDiagBadge();
}
async function refreshLiveGraph(){
  if(dragging){setTimeout(refreshLiveGraph,400);return}
  const previous=selectedNode?byId[selectedNode]:null;
  const graph=await api("/api/graph");
  applyGraph(graph);
  if(previous&&!selectedNode)toast("Узел пропал из кода — выбор снят");
  if(previous&&selectedNode&&byId[selectedNode]&&formDirty&&byId[selectedNode].file_hash!==previous.file_hash){
    setSaveState("conflict");showConflict(byId[selectedNode],null);
    return;
  }
  if(!formDirty)rerenderActive();
  else if(tab!=="scenario")rerenderActive();
  schedulePersist();
}
async function liveLoop(){
  while(liveStarted){
    try{
      const data=await api("/api/wait?rev="+studioRevision);
      liveFailures=0;setLive(true);
      if(data&&Number(data.revision)>studioRevision){
        studioRevision=Number(data.revision);
        await refreshLiveGraph();
      }
    }catch(_e){
      liveFailures+=1;setLive(false);
      await new Promise(done=>setTimeout(done,Math.min(5000,800*liveFailures)));
    }
  }
}
function startLive(){
  if(liveStarted)return;
  liveStarted=true;liveLoop();
}
function renderDomainChips(){
  const box=$("domainChips");if(!box)return;
  const names=[...new Set((G.nodes||[]).filter(n=>n.kind==="endpoint").flatMap(n=>String(n.domain||NO_DOMAIN).split(",").map(s=>s.trim()).filter(Boolean)))].sort();
  box.innerHTML=names.map(name=>`<button type="button" class="chip${domainFilter.has(name)?" acc":""}" data-domain="${esc(name)}">${esc(name)}</button>`).join("");
  box.querySelectorAll("button").forEach(button=>{
    button.onclick=()=>{
      const name=button.dataset.domain;
      if(domainFilter.has(name))domainFilter.delete(name);else domainFilter.add(name);
      renderMap();
    };
  });
}
function showAbout(){
  if(!studioInfo){toast("Нет данных о проекте");return}
  const env=studioInfo.env||{},counts=env.counts||{};
  modal("О проекте",
    `<div class="kv"><span>корень</span><b>${esc(studioInfo.project_root)}</b></div>`+
    `<div class="kv"><span>версия</span><b>${esc(studioInfo.version)}</b></div>`+
    `<div class="kv"><span>ревизия</span><b>${esc(studioInfo.revision)} · ${esc(studioInfo.scanning?"скан идёт":"готово")}</b></div>`+
    `<div class="kv"><span>скан</span><b>${esc(studioInfo.generated_at||"")}</b></div>`+
    `<div class="kv"><span>счётчики</span><b>${esc(Object.entries(counts).map(([k,v])=>k+": "+v).join(" · "))}</b></div>`+
    `<div class="kv"><span>роуты</span><b>${esc(env.routes_package||"")}</b></div>`+
    `<div class="kv"><span>сценарии</span><b>${esc(env.core_discover||"")}</b></div>`+
    `<div class="kv"><span>модели</span><b>${esc(env.models||"")}</b></div>`,
    [{label:"Закрыть",cls:"primary",action:()=>{}}]);
}
function updateDiagBadge(){
  const badge=$("diagBtn");if(!badge)return;
  const diagnostics=(G&&G.diagnostics)||[];
  const errors=diagnostics.filter(d=>d.level==="error").length;
  const warnings=diagnostics.filter(d=>d.level==="warning").length;
  const total=errors+warnings;
  badge.textContent=total?`⚠ ${total}`:"⚠ 0";
  badge.classList.toggle("err",errors>0);
  badge.classList.toggle("warn",errors===0&&warnings>0);
  badge.title=total?`Диагностика: ${errors} ошибок · ${warnings} предупреждений`:"Диагностики нет";
}
function openDiagnostics(){
  const diagnostics=(G&&G.diagnostics)||[];
  const rows=diagnostics.map(d=>{
    const where=d.file?`${esc(d.file)}:${d.line}`:"";
    return `<div class="diag-row ${esc(d.level)}"><span class="chip">${esc(d.code)}</span>`+
      `<div><div>${esc(d.message)}</div>${where?`<button class="link diag-open" data-file="${esc(d.file)}" data-line="${d.line}">${where}</button>`:""}`+
      `${d.hint?`<div class="hint">${esc(d.hint)}</div>`:""}</div></div>`;
  }).join("");
  const body=diagnostics.length?rows:'<p class="hint">Диагностики нет: все найденные конструкции поддержаны.</p>';
  modal("Диагностика",`<div id="diagList">${body}</div>`,[
    {label:"Копировать всё",cls:"",action:()=>{
      const markdown=diagnostics.map(d=>`- ${d.code} ${d.file}:${d.line} — ${d.message}${d.hint?" ("+d.hint+")":""}`).join("\n");
      navigator.clipboard&&navigator.clipboard.writeText(markdown).then(()=>toast("Скопировано")).catch(()=>{});
    }},
    {label:"Закрыть",cls:"primary",action:()=>{}},
  ]);
  document.querySelectorAll(".diag-open").forEach(button=>{
    button.onclick=()=>{
      const node={file_path:button.dataset.file,line:Number(button.dataset.line)||1,planned:false};
      openEditorForNode(node);
    };
  });
}
/* ---------- ТЗ для AI-агента ---------- */
function updateBriefBadge(){
  const button=$("briefBtn");if(!button)return;
  const info=verifyLabel(briefReport,G);
  button.textContent=(info.urgent?"● ":"")+info.text;
  button.title=info.title;
  button.classList.toggle("err",!!info.urgent);
  button.classList.toggle("warn",false);
}
function scheduleBriefStatus(){
  if(briefStatusTimer)clearTimeout(briefStatusTimer);
  briefStatusTimer=setTimeout(refreshBriefStatus,300);
}
async function refreshBriefStatus(){
  try{
    briefReport=await api("/api/verify?brief=last");
  }catch(e){
    if(e.status===404)briefReport=null;else return;
  }
  updateBriefBadge();
}
function briefNodeChoice(node,selected){
  const ready=!!String(node.description||"").trim();
  const hint=ready?"готов к реализации":"нет описания — заполните в студии";
  return `<label class="brief-node${ready?"":" disabled"}" title="${esc(node.id)}">
    <input type="checkbox" data-bid="${esc(node.id)}" ${selected.has(node.id)&&ready?"checked":""} ${ready?"":"disabled"}>
    <span><b>${esc(KIND_NAMES[node.kind]||node.kind)} ${esc(anchorText(node))}</b>
    <small>${esc(node.file_path)} · ${esc(hint)}</small></span></label>`;
}
function briefStatusList(report){
  if(!report||!report.items||!report.items.length)return '<p class="hint">Отчёт пуст.</p>';
  return report.items.map(item=>{
    const cls=item.status==="выполнено"?"ok":item.status==="частично"?"warn":"err";
    const where=item.file?`<button class="link diag-open" data-file="${esc(item.file)}" data-line="${item.line||1}">${esc(item.file)}:${esc(item.line||1)}</button>`:"";
    return `<div class="brief-status-row ${cls}"><span class="chip">T${esc(item.number)}</span><div><b>${esc(KIND_NAMES[item.kind]||item.kind)} ${esc(item.anchor)}</b> — ${esc(item.status)} ${where}<div class="hint">${esc(item.message||"")}</div></div></div>`;
  }).join("");
}
function openBriefStatus(){
  if(!briefReport){openBriefDialog();return}
  modal("ТЗ для AI-агента",`<div class="brief-stats">Выполнено ${briefReport.done} из ${briefReport.total} · ${esc(statusText(briefReport))}</div>${briefStatusList(briefReport)}`,[
    {label:"Открыть ТЗ",action:()=>openBriefDialog()},
    {label:"Закрыть",cls:"primary",action:()=>{}},
  ]);
  document.querySelectorAll(".diag-open").forEach(button=>{
    button.onclick=()=>openEditorForNode({file_path:button.dataset.file,line:Number(button.dataset.line)||1,planned:false});
  });
}
async function openBriefDialog(){
  await flushPending();
  const nodes=plannedNodes(G);
  const includeEmpty=false;
  const selected=new Set(nodes.filter(node=>String(node.description||"").trim()).map(node=>node.id));
  const mapMode=defaultMapMode(((G&&G.nodes)||[]).length);
  const overlay=document.createElement("div");
  overlay.className="studio-modal";
  overlay.innerHTML=`<div class="modal-box brief-box"><h3>ТЗ для AI-агента</h3>
    <div class="brief-layout">
      <div class="brief-left">
        <div class="brief-controls">
          <label class="hint">Карта:
            <select id="briefMap">
              <option value="related">связанная</option>
              <option value="full">вся</option>
              <option value="compact">кратко</option>
              <option value="none">нет</option>
            </select>
          </label>
          <label class="hint"><input type="checkbox" id="briefEmpty"> включать узлы без описания</label>
          <div class="hint">Планируемые узлы: ${nodes.length} · отмечены готовые.</div>
        </div>
        <div id="briefNodes">${nodes.length?nodes.map(node=>briefNodeChoice(node,selected)).join(""):'<p class="hint">Планируемых узлов нет. Добавьте их во вкладке «Создание».</p>'}</div>
      </div>
      <div class="brief-preview">
        <div class="brief-stats" id="briefStats">Подготовка…</div>
        <pre id="briefPreview">Собираю ТЗ…</pre>
        <div class="hint">ТЗ содержит фрагменты вашего кода и описания. Перед отправкой во внешний AI-сервис это решает пользователь.</div>
        <div class="brief-actions">
          <button type="button" id="briefCopy">Скопировать ТЗ</button>
          <button type="button" id="briefPrompt">Скопировать короткий промпт</button>
          <button type="button" id="briefDownload">Скачать .md</button>
          <button type="button" class="primary" id="briefSave">Сохранить в проект</button>
          <button type="button" id="briefClose">Закрыть</button>
        </div>
      </div>
    </div></div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener("click",ev=>{if(ev.target===overlay)overlay.remove()});
  const mapSelect=overlay.querySelector("#briefMap");
  mapSelect.value=briefLast&&briefLast.options&&briefLast.options.map||mapMode;
  const emptyBox=overlay.querySelector("#briefEmpty");
  let includeEmptyValue=includeEmpty;
  emptyBox.checked=includeEmptyValue;
  const preview=overlay.querySelector("#briefPreview");
  const statsBox=overlay.querySelector("#briefStats");
  briefPreview=preview;
  let currentMarkdown="";
  let currentPath=".studio/agent-brief.md";
  const collect=()=>{
    const chosen=[...overlay.querySelectorAll('#briefNodes input[data-bid]:checked')].map(input=>input.dataset.bid);
    return briefRequest({selected:chosen,map:mapSelect.value,includeEmpty:includeEmptyValue});
  };
  const generate=async()=>{
    statsBox.textContent="Собираю ТЗ…";
    try{
      const data=await api("/api/brief","POST",collect());
      currentMarkdown=data.markdown||"";
      briefLast=data;
      preview.textContent=currentMarkdown;
      const stats=data.stats||{};
      statsBox.textContent=`Задач: ${stats.tasks||0} · не готово: ${stats.unready||0} · ${stats.chars||0} символов (~${stats.approx_tokens||0} токенов)`+
        ((data.warnings||[]).length?` · предупреждений: ${data.warnings.length}`:"");
      if((data.warnings||[]).length){
        statsBox.title=data.warnings.join("\n");
        statsBox.textContent+=" · подробности в подсказке";
      }
      if(data.brief_id){
        try{briefReport=await api("/api/verify?brief="+encodeURIComponent(data.brief_id));}catch(_){}
      }
      updateBriefBadge();
    }catch(e){
      statsBox.textContent="Ошибка: "+e.message;
    }
  };
  const scheduleGenerate=()=>{clearTimeout(overlay._timer);overlay._timer=setTimeout(generate,220)};
  overlay.querySelectorAll('#briefNodes input[data-bid]').forEach(input=>input.onchange=scheduleGenerate);
  mapSelect.onchange=scheduleGenerate;
  emptyBox.onchange=()=>{
    includeEmptyValue=emptyBox.checked;
    nodes.forEach(node=>{
      const input=[...overlay.querySelectorAll("#briefNodes input[data-bid]")].find(el=>el.dataset.bid===node.id);
      if(!input)return;
      const ready=!!String(node.description||"").trim();
      if(!ready){
        input.disabled=!includeEmptyValue;
        if(includeEmptyValue)input.checked=true;
      }
    });
    scheduleGenerate();
  };
  const copyText=(text,message)=>{
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(()=>toast(message)).catch(()=>toast("Не удалось скопировать"));
      return;
    }
    const area=document.createElement("textarea");
    area.value=text;area.style.position="fixed";area.style.opacity="0";document.body.appendChild(area);area.select();
    try{document.execCommand("copy");toast(message)}catch(_){toast("Не удалось скопировать")}
    area.remove();
  };
  overlay.querySelector("#briefCopy").onclick=()=>copyText(currentMarkdown||preview.textContent,"ТЗ скопировано");
  overlay.querySelector("#briefPrompt").onclick=()=>copyText(shortPrompt(currentPath),"Промпт скопирован");
  overlay.querySelector("#briefDownload").onclick=()=>{
    const blob=new Blob([currentMarkdown||preview.textContent],{type:"text/markdown;charset=utf-8"});
    const link=document.createElement("a");
    link.href=URL.createObjectURL(blob);link.download="agent-brief.md";document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  };
  overlay.querySelector("#briefSave").onclick=async()=>{
    try{
      const data=await api("/api/brief/save","POST",collect());
      currentPath=data.path||currentPath;
      toast("ТЗ сохранено: "+currentPath);
      if(data.brief_id){
        try{briefReport=await api("/api/verify?brief="+encodeURIComponent(data.brief_id));}catch(_){}
        updateBriefBadge();
      }
    }catch(e){toast("Ошибка сохранения ТЗ: "+e.message)}
  };
  overlay.querySelector("#briefClose").onclick=()=>{clearTimeout(overlay._timer);overlay.remove()};
  await generate();
}
function modal(title,bodyHtml,buttons){
  document.querySelectorAll(".studio-modal").forEach(el=>el.remove());
  const overlay=document.createElement("div");
  overlay.className="studio-modal";
  overlay.innerHTML=`<div class="modal-box"><h3>${esc(title)}</h3><div class="modal-body">${bodyHtml}</div><div class="modal-actions"></div></div>`;
  const actions=overlay.querySelector(".modal-actions");
  (buttons||[]).forEach(spec=>{
    const button=document.createElement("button");
    button.type="button";button.textContent=spec.label;button.className=spec.cls||"";
    button.onclick=()=>{overlay.remove();if(spec.action)spec.action()};
    actions.appendChild(button);
  });
  overlay.addEventListener("click",ev=>{if(ev.target===overlay)overlay.remove()});
  document.body.appendChild(overlay);
}
function showConflict(node,payload){
  const desc=$("fDesc")?$("fDesc").value:(node.description||"");
  const acc=$("fAcc")?$("fAcc").value.split("\n").map(s=>s.trim()).filter(Boolean):(node.acceptance||[]);
  const dom=$("fDomain")?$("fDomain").value:(node.domain||"");
  const current=(payload&&payload.details&&payload.details.current)||{};
  const currentText=current.description!==undefined?current.description:node.description;
  modal("Файл изменён вне студии",
    `<div class="conflict"><div><h4>Ваши правки</h4><pre>${esc(desc||"(пусто)")}</pre></div>`+
    `<div><h4>Сейчас в коде</h4><pre>${esc(currentText||"(пусто)")}</pre></div></div>`,
    [
      {label:"Сохранить мои правки поверх",cls:"primary",action:()=>saveMetadata(true)},
      {label:"Взять из кода",action:async()=>{applyGraph(await api("/api/graph"));formDirty=false;setSaveState("reset");renderScenario();renderInspector()}},
      {label:"Отмена",action:()=>setSaveState("dirty")},
    ]);
}
function toastAction(message,label,action){
  const t=$("toast");if(!t)return;
  t.innerHTML=`<span>${esc(message)}</span> <button type="button">${esc(label)}</button>`;
  t.style.display="block";clearTimeout(t._h);
  const button=t.querySelector("button");
  if(button)button.onclick=()=>{t.style.display="none";action()};
  t._h=setTimeout(()=>{t.style.display="none"},8000);
}
async function flushPending(){
  if(!formDirty||!selectedNode)return;
  await saveMetadata();
}
function initSearch(){
  const box=$("searchBox");if(!box)return;
  const update=()=>{
    lastSearchQuery=box.value.trim();
    const all=(G.nodes||[]).filter(n=>n.kind==="endpoint");
    const found=all.filter(n=>!lastSearchQuery||matchesAll(lastSearchQuery,[n.label,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description,n.details&&n.details.function_name]));
    const count=$("searchCount");if(count)count.textContent=lastSearchQuery?`найдено ${found.length} из ${all.length}`:"";
    if(tab==="map")renderMap();else if(tab==="scenario")renderScenario();
  };
  box.addEventListener("input",update);
  box.addEventListener("keydown",ev=>{
    if(ev.key!=="Enter")return;
    const all=(G.nodes||[]).filter(n=>n.kind==="endpoint");
    const first=all.find(n=>matchesAll(lastSearchQuery,[n.label,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description]));
    if(first)openEndpointScenario(first.id);
  });
}
function openPalette(){
  const nodes=(G&&G.nodes)||[];
  const overlay=document.createElement("div");
  overlay.className="studio-modal palette";
  overlay.innerHTML='<div class="modal-box"><input id="paletteInput" placeholder="Найти узел: имя, якорь, файл, домен"><div id="paletteList" class="palette-list"></div></div>';
  document.body.appendChild(overlay);
  const input=overlay.querySelector("#paletteInput");
  const list=overlay.querySelector("#paletteList");
  let items=[],index=0;
  const draw=()=>{
    items=searchNodes(nodes,input.value,20);
    index=0;
    list.innerHTML=items.length?items.map((n,i)=>`<div class="palette-item${i===0?" on":""}" data-i="${i}"><span class="chip">${esc(KIND_NAMES[n.kind]||n.kind)}</span> ${esc(n.label)} <span class="hint">${esc(n.file_path)}</span></div>`).join(""):'<div class="hint">Ничего не найдено</div>';
    list.querySelectorAll(".palette-item").forEach(el=>el.onclick=()=>choose(Number(el.dataset.i)));
  };
  const choose=i=>{
    const node=items[i];if(!node)return;
    overlay.remove();
    if(node.kind==="endpoint"){openEndpointScenario(node.id);return}
    if(node.kind==="scenario"){
      selectedScenario=node.id;selectedNode=node.id;setTab("scenario");return;
    }
    const owner=(G.scenarios||[]).find(sid=>{
      const path=G.scenario_paths&&G.scenario_paths[sid];
      return path&&(path.io_calls||[]).includes(node.id)||path&&(path.models||[]).includes(node.id)||path&&(path.tests||[]).includes(node.id);
    });
    if(owner){selectedScenario=owner;selectedNode=node.id;setTab("scenario");renderScenario();renderInspector()}
    else{setTab("scenario");selectedNode=node.id;selectedScenario=null;renderScenario();toast("Узел в разделе «Неполные»")}
  };
  input.addEventListener("input",draw);
  input.addEventListener("keydown",ev=>{
    if(ev.key==="Escape")overlay.remove();
    if(ev.key==="ArrowDown"||ev.key==="ArrowUp"){
      ev.preventDefault();
      if(!items.length)return;
      index=(index+(ev.key==="ArrowDown"?1:-1)+items.length)%items.length;
      list.querySelectorAll(".palette-item").forEach((el,i)=>el.classList.toggle("on",i===index));
      const active=list.querySelector(".palette-item.on");if(active)active.scrollIntoView({block:"nearest"});
    }
    if(ev.key==="Enter")choose(index);
  });
  overlay.addEventListener("click",ev=>{if(ev.target===overlay)overlay.remove()});
  draw();input.focus();
}

/* ---------- инспектор и автосейв ---------- */
function renderInspector(){
  const chips=$("inspChips"),body=$("inspBody"),foot=$("inspFoot");
  chips.innerHTML="";body.innerHTML="";
  if(!selectedNode||!byId[selectedNode]){
    body.innerHTML='<p class="hint">Выберите узел на карте сценария. Описание, критерии и домен хранятся маркерами в коде.</p>';
    foot.textContent="Правки сохраняются при потере фокуса.";return;
  }
  const n=byId[selectedNode];
  chips.innerHTML=`<span class="chip acc">${esc(KIND_NAMES[n.kind]||n.kind)}</span>${n.planned?'<span class="chip plan">без кода</span>':""}<span class="chip">${esc(n.file_path)}${n.planned?"":":"+n.line}</span>`;
  let h="";
  h+=`<div class="kv"><span>якорь</span><b>${esc(anchorText(n))}</b></div>`;
  h+=`<div class="insp-actions"><button id="openEditorBtn" type="button">Открыть в редакторе</button><button id="copyLocBtn" type="button">Копировать путь:строка</button></div>`;
  h+=`<div class="note">Если ничего не произошло — выберите другой редактор в шапке или проверьте, что протокол редактора зарегистрирован.</div>`;
  const extraRoutes=(n.details&&n.details.extra_routes)||[];
  if(extraRoutes.length){
    h+=`<div class="field"><label>Ещё маршруты</label><div class="hint" style="line-height:1.6">`+
      extraRoutes.map(route=>`${esc(String(route.http_method||"").toUpperCase())} ${esc(route.url_path||route.path||"")}`).join("<br>")+
      `</div></div>`;
  }
  h+=`<div class="field"><label>Описание</label><textarea id="fDesc" rows="5">${esc(n.description)}</textarea></div>`;
  h+=`<div class="field"><label>Критерии приёмки (по одному на строку)</label><textarea id="fAcc" rows="5">${esc((n.acceptance||[]).join("\n"))}</textarea></div>`;
  if(n.kind==="endpoint"){
    h+=`<div class="field"><label>Домен</label><input id="fDomain" value="${esc(n.domain)}" placeholder="например, orders"><div class="note">Домен назначается только эндпоинту; остальные узлы наследуют его автоматически.</div></div>`;
  }else{
    h+=`<div class="field"><label>Домен (наследуется)</label><input value="${esc(n.domain)}" disabled><div class="note">Домен нельзя назначить этому узлу: он наследуется от связанных эндпоинтов.</div></div>`;
  }
  if(n.planned){
    h+=`<div class="field"><div class="note">Этот узел существует только в метаданных: кода с таким якорем ещё нет. Правки сохраняются в файл метаданных.</div></div>`;
  }
  if((n.peripherals||[]).length){
    h+=`<div class="field"><label>Периферийные узлы</label><div class="hint" style="line-height:1.6">`+
      n.peripherals.map(p=>`${esc((p.symbols||[]).join(", ")||p.name)} — ${esc(ROLE_NAMES[p.role]||p.role||"")} · ${esc(p.name)}`).join("<br>")+
      `</div></div>`;
  }
  body.innerHTML=h;
  const openBtn=$("openEditorBtn");if(openBtn)openBtn.onclick=()=>openEditorForNode(n);
  const copyBtn=$("copyLocBtn");if(copyBtn)copyBtn.onclick=()=>copyNodeLocation(n);
  [["fDesc","description"],["fAcc","acceptance"],["fDomain","domain"]].forEach(([id,_f])=>{
    const el=$(id);if(!el)return;
    el.addEventListener("input",()=>{formDirty=true;setSaveState("edit")});
    el.addEventListener("blur",()=>saveMetadata());
    el.addEventListener("keydown",ev=>{if(ev.key==="Enter"&&(ev.ctrlKey||ev.metaKey))el.blur()});
  });
  formDirty=false;
  foot.textContent=saveLabel(saveState)||"Автосейв при потере фокуса. Пустой раздел удаляется из кода.";
}
function anchorText(n){
  const d=n.details||{};
  if(n.kind==="endpoint")return d.function_name||"";
  if(n.kind==="scenario"||n.kind==="model")return d.class_name||"";
  if(n.kind==="test")return d.test_name||"";
  if(n.kind==="io")return n.planned?`${d.scenario_class}._db.${d.method}`:`${d.scenario_class}._db.${d.method}#${d.ordinal||1}`;
  return "";
}
async function saveMetadata(force=false){
  const nodeId=selectedNode;
  const n=byId[nodeId];if(!n)return;
  const desc=$("fDesc")?$("fDesc").value:n.description;
  const acc=$("fAcc")?$("fAcc").value.split("\n").map(s=>s.trim()).filter(Boolean):n.acceptance;
  const dom=$("fDomain")?$("fDomain").value:(n.kind==="endpoint"?n.domain:"");
  const same=desc===n.description&&JSON.stringify(acc)===JSON.stringify(n.acceptance)&&(n.kind!=="endpoint"||dom===n.domain);
  if(same&&!force){formDirty=false;setSaveState("saved");return}
  setSaveState("save");
  try{
    const data=await api("/api/metadata/node","POST",{
      node_id:nodeId,description:desc,acceptance:acc,domain:dom,
      base_hash:n.file_hash||"",force:!!force,
    });
    applyGraph(data.graph);
    if(!byId[nodeId]){
      selectedNode=null;formDirty=false;setSaveState("reset");
      toast("Узел пропал из кода — выбор снят");
      renderScenario();schedulePersist();return;
    }
    selectedNode=nodeId;
    formDirty=false;setSaveState("saved");
    if((data.removed_orphans||[]).length&&data.undo_id){
      toastAction(`Удалено устаревших блоков: ${data.removed_orphans.length}`,"Отменить",async()=>{
        try{
          const undone=await api("/api/undo","POST",{undo_id:data.undo_id});
          applyGraph(undone.graph);renderScenario();renderInspector();toast("Удаление отменено");
        }catch(e){toast("ошибка отмены: "+e.message)}
      });
    }else{
      toast("метаданные сохранены");
    }
    if(tab==="scenario"){renderScenario();renderInspector()}else renderMap();
    schedulePersist();
  }catch(e){
    if(e.code==="file_changed"&&!force){setSaveState("conflict");showConflict(n,e);return}
    setSaveState("fail");toast("ошибка сохранения: "+e.message);
  }
}

/* ---------- Создание: графический конструктор ---------- */
function createNodeById(id){return createState.nodes.find(n=>n.id===id)}
function createNodeLabel(n){const name=String(n.name||"").trim();return name||`новый ${KIND_NAMES[n.kind]||n.kind}`}
function createBoxes(){
  const stage=$("createStage"),out={};
  stage.querySelectorAll(".create-card[data-cid]").forEach(e=>{
    const p=offsetInStage(e,stage);
    out[e.dataset.cid]={x:p.x,y:p.y,w:e.offsetWidth,h:e.offsetHeight};
  });
  return out;
}
function recomputeCreateSizes(){
  const stage=$("createStage"),svg=$("createEdges");
  stage.querySelectorAll(".create-zone").forEach(zone=>{
    const size=areaSize(zone);
    zone.style.width=size.w+"px";zone.style.height=size.h+"px";
  });
  sizeStage(stage,svg);
}
function createZones(){return[...$("createStage").querySelectorAll(".create-zone")]}
function drawCreateEdges(){
  const svg=$("createEdges");
  if(!svg)return;
  drawEdges(svg,createBoxes(),createState.edges.map(e=>({from_node:e.from,to_node:e.to,kind:e.kind})));
}

function renderCreateEdgeList(){
  const box=$("createEdgeList");if(!box)return;
  box.innerHTML="";
  if(!createState.edges.length){
    box.innerHTML='<p class="hint">Связей нет. Потяните за точку справа от узла.</p>';
    return;
  }
  createState.edges.forEach((edge,index)=>{
    const from=createNodeById(edge.from),to=createNodeById(edge.to);
    const row=document.createElement("div");row.className="edge-item";
    row.innerHTML=`<span class="names" title="${esc((from?createNodeLabel(from):"?")+" → "+(to?createNodeLabel(to):"?"))}">${esc(from?createNodeLabel(from):"?")} → ${esc(to?createNodeLabel(to):"?")}</span>`+
      `<select>${Object.keys(EDGE_NAMES).map(k=>`<option value="${k}">${EDGE_NAMES[k]}</option>`).join("")}</select>`+
      `<button type="button" title="удалить связь">×</button>`;
    const sel=row.querySelector("select");sel.value=edge.kind;
    sel.onchange=()=>{edge.kind=sel.value;drawCreateEdges()};
    row.querySelector("button").onclick=()=>{createState.edges.splice(index,1);drawCreateEdges();renderCreateEdgeList()};
    box.appendChild(row);
  });
}
function renderCreateGraph(){
  const stage=$("createStage"),svg=$("createEdges");
  clearStage(stage,svg);
  if(!createState.nodes.length){
    const e=document.createElement("div");e.className="empty";e.style.top="8px";
    e.innerHTML="Добавьте узел кнопкой «+ узел» — он появится в области своего вида.";
    stage.appendChild(e);
  }
  const defs=[["endpoint","эндпоинт"],["scenario","сценарий"],["io","io"],["model","модель"],["test","тест"]];
  const zones={};
  defs.forEach(([kind,title])=>{
    const zone=document.createElement("div");
    zone.className="zone create-zone";zone.dataset.kind=kind;
    if(!createState.zones[kind])createState.zones[kind]={x:CREATE_LAYER_X[kind],y:40};
    const pos=createState.zones[kind];
    zone.style.left=pos.x+"px";zone.style.top=pos.y+"px";
    zone.style.width="240px";zone.style.height="70px";
    const lab=document.createElement("div");lab.className="zlab";
    lab.innerHTML=`<span class="grip" title="перетащить">⠿</span>${esc(title)} <span class="hint">· 0</span><button type="button" class="zone-add" data-kind="${kind}" title="Добавить узел">+</button>`;
    zone.appendChild(lab);stage.appendChild(zone);zones[kind]=zone;
    lab.querySelector(".zone-add").onclick=ev=>{ev.stopPropagation();addCreateNode(kind)};
    enableZoneDrag(zone,createView,
      (nx,ny)=>{
        const cx=Math.max(10,nx),cy=Math.max(10,ny);
        const rect={x:cx,y:cy,w:zone.offsetWidth,h:zone.offsetHeight};
        if(zoneOverlaps(zone,rect,createZones()))return;
        zone.style.left=cx+"px";zone.style.top=cy+"px";
        createState.zones[kind]={x:cx,y:cy};drawCreateEdges();
      },
      ()=>{
        createState.zones[kind]={x:parseFloat(zone.style.left)||0,y:parseFloat(zone.style.top)||0};
        recomputeCreateSizes();drawCreateEdges();
      });
  });
  createState.nodes.forEach(node=>{
    const zone=zones[node.kind];
    if(zone)zone.appendChild(createCardElement(node,zone));
  });
  defs.forEach(([kind])=>{
    const zone=zones[kind];
    const hint=zone.querySelector(".zlab .hint");
    if(hint)hint.textContent="· "+zone.querySelectorAll(".create-card[data-cid]").length;
  });
  recomputeCreateSizes();
  drawCreateEdges();
  applyTransform("createView",createView);
  renderCreateEdgeList();
}
function createCardElement(node,zone){
  const e=document.createElement("div");
  e.className=`card create-card k-${node.kind}`;
  e.dataset.cid=node.id;e.dataset.kind=node.kind;
  e.style.left=node.x+"px";e.style.top=node.y+"px";
  const http=String((node.details||{}).http_method||"").toUpperCase();
  const extra=node.kind==="endpoint"
    ? `<label>HTTP и путь (необязательно)</label><div class="two"><select data-f="http"><option value="" ${http===""?"selected":""}>не указан</option>${["GET","POST","PUT","PATCH","DELETE"].map(m=>`<option value="${m}" ${m===http?"selected":""}>${m}</option>`).join("")}</select><input data-f="path" value="${esc((node.details||{}).path||"")}" placeholder="/path (необязательно)"></div>`+
      `<label>Домен</label><input data-f="domain" value="${esc(node.domain||"")}" placeholder="orders">`
    : node.kind==="io"
      ? `<label>Сценарий (class name)</label><input data-f="scenario" value="${esc((node.details||{}).scenario||"")}" placeholder="CreateOrderScenario">`
      : "";
  e.innerHTML=`<div class="hd"><span class="grip" title="перетащить">⠿</span><span class="chip">${esc(KIND_NAMES[node.kind]||node.kind)}</span><span class="t">${esc(createNodeLabel(node))}</span><button type="button" class="del" title="удалить узел">×</button></div>`+
    `<div class="cbody">`+
      `<label>Вид узла</label><select data-f="kind">${CREATE_KINDS.map(k=>`<option value="${k}" ${k===node.kind?"selected":""}>${KIND_NAMES[k]}</option>`).join("")}</select>`+
      `<label>Имя / якорь</label><div class="tip-wrap" data-tip="Имя элемента кода, к которому привяжутся метаданные: функция для эндпоинта, класс для сценария/модели, тест-функция для теста, метод порта для io. Когда код появится, узел будет найден по этому имени."><input data-f="name" value="${esc(node.name||"")}" placeholder="имя элемента кода"></div>`+
      `<label>Описание</label><textarea data-f="description" rows="2" placeholder="описание узла">${esc(node.description||"")}</textarea>`+
      `<label>Критерии приёмки (по одной на строку)</label><textarea data-f="acceptance" rows="2" placeholder="- критерий">${esc(node.acceptance||"")}</textarea>`+
      extra+
    `</div><div class="connector" title="потяните к другому узлу, чтобы создать связь"></div>`;

  e.querySelector(".del").onclick=ev=>{ev.stopPropagation();deleteCreateNode(node.id)};
  e.querySelector('[data-f="kind"]').onchange=ev=>{
    node.kind=ev.target.value;
    node.x=14;
    node.y=nextCreateY(node.kind);
    renderCreateGraph();
  };
  const bind=(sel,key,after)=>{
    const el=e.querySelector(sel);if(!el)return;
    el.addEventListener("input",()=>{node[key]=el.value;if(after)after()});
  };
  bind('[data-f="name"]',"name",()=>{
    const t=e.querySelector(".hd .t");if(t)t.textContent=createNodeLabel(node);
    renderCreateEdgeList();
  });
  bind('[data-f="description"]',"description");
  bind('[data-f="acceptance"]',"acceptance");
  if(node.kind==="endpoint"){
    const httpEl=e.querySelector('[data-f="http"]');
    httpEl.onchange=()=>{node.details.http_method=httpEl.value.toLowerCase()};
    const pathEl=e.querySelector('[data-f="path"]');
    pathEl.addEventListener("input",()=>{node.details.path=pathEl.value});
    const domEl=e.querySelector('[data-f="domain"]');
    domEl.addEventListener("input",()=>{node.domain=domEl.value});
  }
  if(node.kind==="io"){
    const scEl=e.querySelector('[data-f="scenario"]');
    scEl.addEventListener("input",()=>{node.details.scenario=scEl.value});
  }
  e.querySelector(".connector").addEventListener("mousedown",ev=>startCreateConnection(ev,node,e));
  enableNodeDrag(e,"create",node.id,node.x,node.y,createView,
    (nx,ny)=>{
      const zone=e.parentElement;if(!zone)return;
      const cx=Math.max(14,nx),cy=Math.max(34,ny);
      if(proposedOverlap(zone,[e],[{el:e,x:cx,y:cy}]))return;
      const size=areaSize(zone,new Map([[e,{x:cx,y:cy}]]));
      if(zoneOverlaps(zone,proposedZoneRect(zone,size),createZones()))return;
      e.style.left=cx+"px";e.style.top=cy+"px";
      node.x=cx;node.y=cy;
      recomputeCreateSizes();drawCreateEdges();
    },
    ()=>{recomputeCreateSizes();drawCreateEdges()});
  return e;
}
function nextCreateY(kind){
  const zone=$("createStage").querySelector(`.create-zone[data-kind="${kind}"]`);
  let y=38;
  if(zone){
    zone.querySelectorAll(".create-card[data-cid]").forEach(card=>{
      y=Math.max(y,(parseFloat(card.style.top)||0)+card.offsetHeight+12);
    });
  }
  return y;
}
function addCreateNode(kind){
  if(!CREATE_KINDS.includes(kind))return;
  const node={
    id:"c"+(createState.seq++),kind,name:"",description:"",acceptance:"",domain:"",
    details:{http_method:"",path:"",scenario:""},
    x:14,y:nextCreateY(kind)
  };
  createState.nodes.push(node);
  renderCreateGraph();
}
function deleteCreateNode(id){
  createState.nodes=createState.nodes.filter(n=>n.id!==id);
  createState.edges=createState.edges.filter(e=>e.from!==id&&e.to!==id);
  renderCreateGraph();
}
function startCreateConnection(ev,node,card){
  if(ev.button!==0)return;
  ev.preventDefault();ev.stopPropagation();
  const svg=$("createEdges"),canvas=$("createCanvas"),stage=$("createStage");
  const origin=offsetInStage(card,stage);
  const x1=origin.x+card.offsetWidth;
  const y1=origin.y+card.offsetHeight/2;
  const path=document.createElementNS("http://www.w3.org/2000/svg","path");
  path.setAttribute("fill","none");path.setAttribute("stroke","#e8833a");
  path.setAttribute("stroke-width","2");path.setAttribute("stroke-dasharray","6,4");
  path.setAttribute("pointer-events","none");
  svg.appendChild(path);
  const rect=canvas.getBoundingClientRect();
  const move=e=>{
    const x2=(e.clientX-rect.left-createView.x)/(createView.k||1);
    const y2=(e.clientY-rect.top-createView.y)/(createView.k||1);
    path.setAttribute("d",`M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2}`);
  };
  const up=e=>{
    window.removeEventListener("mousemove",move);
    window.removeEventListener("mouseup",up);
    path.remove();
    const el=document.elementFromPoint(e.clientX,e.clientY);
    const targetCard=el&&el.closest?el.closest(".create-card[data-cid]"):null;
    if(targetCard&&targetCard.dataset.cid!==node.id){
      const target=createNodeById(targetCard.dataset.cid);
      if(target){
        if(createState.edges.some(edge=>edge.from===node.id&&edge.to===target.id)){
          toast("Такая связь уже есть");
        }else{
          createState.edges.push({from:node.id,to:target.id,kind:inferEdgeKind(node.kind,target.kind)});
          drawCreateEdges();renderCreateEdgeList();
        }
      }
    }
    createConnect=null;
  };
  createConnect={from:node.id};
  window.addEventListener("mousemove",move);
  window.addEventListener("mouseup",up);
}
function renderCreate(){
  renderCreateGraph();
}
function collectCreate(){
  if(!createState.nodes.length)throw new Error("Добавьте хотя бы один узел");
  const names=createState.nodes.map(n=>String(n.name||"").trim());
  if(names.some(n=>!n))throw new Error("Укажите имя (якорь) у каждого узла");
  if(new Set(names).size!==names.length)throw new Error("Имена узлов не должны повторяться");
  const nodes=createState.nodes.map((n,index)=>({
    kind:n.kind,
    name:names[index],
    description:n.description||"",
    acceptance:String(n.acceptance||"").split("\n").map(s=>s.trim()).filter(Boolean),
    domain:n.kind==="endpoint"?(n.domain||""):"",
    details:n.kind==="endpoint"
      ? {http_method:String(n.details.http_method||"post").toLowerCase(),path:String(n.details.path||"").trim()}
      : n.kind==="io"
        ? {scenario:String(n.details.scenario||"").trim()}
        : {}
  }));
  const byId=createState.nodes.reduce((acc,n)=>{acc[n.id]=n;return acc},{});
  const edges=createState.edges.map(e=>({
    from:String((byId[e.from]&&byId[e.from].name)||"").trim(),
    to:String((byId[e.to]&&byId[e.to].name)||"").trim(),
    kind:e.kind
  })).filter(e=>e.from&&e.to);
  return {nodes,edges};
}
async function submitCreate(){
  const result=$("createResult");result.style.display="none";
  try{
    const data=await api("/api/create","POST",collectCreate());
    applyGraph(data.graph);
    createState.nodes=[];createState.edges=[];createState.seq=1;
    renderCreateGraph();
    result.style.display="block";
    const lines=["Созданы файлы только с метаданными:"];
    (data.created||[]).forEach(c=>lines.push(`  ${c.path}  — ${KIND_NAMES[c.kind]||c.kind} ${c.name}`));
    lines.push("");lines.push("Кода в файлах нет: это TODO для разработчика. После написания кода узлы появятся на карте.");
    result.textContent=lines.join("\n");
    toastAction("файлы созданы","Сформировать ТЗ",()=>openBriefDialog());
  }catch(e){
    result.style.display="block";result.textContent="Ошибка: "+e.message;
  }
}

/* ---------- инициализация ---------- */
function activeView(){
  if(tab==="map")return{view:mapView,id:"mapView",canvas:$("mapCanvas")};
  if(tab==="scenario")return{view:scenView,id:"scenView",canvas:$("scenCanvas")};
  return{view:createView,id:"createView",canvas:$("createCanvas")};
}
async function boot(){
  try{
    applyGraph(await api("/api/graph"));
  }catch(e){toast("не удалось загрузить граф: "+e.message);G={nodes:[],edges:[],scenarios:[],scenario_paths:{},diagnostics:[]}}
  try{
    studioInfo=await api("/api/state");
    studioRevision=Number(studioInfo.revision)||studioRevision;
    document.title="studio · "+String(studioInfo.project_root||"").replace(/[\\/]+$/,"").split(/[\\/]/).pop();
  }catch(_){ }
  initUiStorage();
  initEditorChoice();
  bindViewport("mapCanvas",mapView,()=>applyTransform("mapView",mapView));
  bindViewport("scenCanvas",scenView,()=>applyTransform("scenView",scenView));
  bindViewport("createCanvas",createView,()=>applyTransform("createView",createView));
  $("scenCanvas").addEventListener("click",ev=>{
    if(tab!=="scenario")return;
    if(Date.now()-justDraggedAt<250)return;
    if(ev.target.closest(".card")||ev.target.closest(".zlab"))return;
    if(selectedNode!==null){flushPending();selectedNode=null;renderScenario();schedulePersist()}
  });
  document.addEventListener("keydown",ev=>{
    const tag=(ev.target&&ev.target.tagName||"").toLowerCase();
    const typing=tag==="input"||tag==="textarea"||tag==="select"||ev.target.isContentEditable;
    if(ev.key==="Escape"&&tab==="scenario"&&selectedNode!==null&&!typing){flushPending();selectedNode=null;renderScenario()}
    if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==="k"){ev.preventDefault();openPalette();return}
    if(typing)return;
    if(ev.key==="/") {ev.preventDefault();const box=$("searchBox");if(box){box.focus();box.select()}}
    if(ev.key.toLowerCase()==="o"&&selectedNode){ev.preventDefault();openEditorForNode(byId[selectedNode])}
    if(ev.key.toLowerCase()==="f"&&!ev.ctrlKey&&!ev.metaKey){ev.preventDefault();const a=activeView();fitView(a.canvas,a.view,()=>applyTransform(a.id,a.view))}
    if(ev.key==="+"||ev.key==="=") {ev.preventDefault();const a=activeView();a.view.k=Math.min(2.5,a.view.k*1.15);applyTransform(a.id,a.view)}
    if(ev.key==="-") {ev.preventDefault();const a=activeView();a.view.k=Math.max(.2,a.view.k*.87);applyTransform(a.id,a.view)}
    if(ev.key.toLowerCase()==="h") {ev.preventDefault();hideMeta=!hideMeta;if($("hideMeta"))$("hideMeta").checked=hideMeta;if(tab==="map")renderMap();else if(tab==="scenario")renderScenario();schedulePersist()}
    if(ev.key==="?") {ev.preventDefault();modal("Горячие клавиши","<div class=hotkeys><div><b>Esc</b> снять выделение</div><div><b>/</b> поиск</div><div><b>Ctrl+K</b> палитра узлов</div><div><b>O</b> открыть в редакторе</div><div><b>F</b> вписать</div><div><b>+ / −</b> зум</div><div><b>H</b> скрыть метаданные</div><div><b>Ctrl+Enter</b> сохранить поле</div></div>",[{label:"Сбросить раскладку",action:()=>{try{if(uiStorage)uiStorage.reset()}catch(_){ }location.reload()}},{label:"Закрыть",cls:"primary",action:()=>{}}])}
  });
  $("zIn").onclick=()=>{const a=activeView();a.view.k=Math.min(2.5,a.view.k*1.15);applyTransform(a.id,a.view);schedulePersist()};
  $("zOut").onclick=()=>{const a=activeView();a.view.k=Math.max(.2,a.view.k*.87);applyTransform(a.id,a.view);schedulePersist()};
  $("zReset").onclick=()=>{const a=activeView();a.view.k=1;a.view.x=30;a.view.y=30;applyTransform(a.id,a.view);schedulePersist()};
  $("zFit").onclick=()=>{const a=activeView();fitView(a.canvas,a.view,()=>applyTransform(a.id,a.view));schedulePersist()};
  $("hideMeta").onchange=()=>{hideMeta=$("hideMeta").checked;if(tab==="map")renderMap();else if(tab==="scenario")renderScenario();schedulePersist()};
  $("rescan").onclick=async()=>{
    await flushPending();
    try{
      applyGraph(await api("/api/rescan","POST",{}));
      if(tab==="map")renderMap();else if(tab==="scenario")renderScenario();else renderCreate();
      updateDiagBadge();toast("карта обновлена");schedulePersist();
    }catch(e){toast("ошибка скана: "+e.message)}
  };
  $("diagBtn").onclick=openDiagnostics;
  if($("briefBtn"))$("briefBtn").onclick=()=>{if(briefReport&&briefReport.total>0)openBriefStatus();else openBriefDialog()};
  if($("logoBtn"))$("logoBtn").onclick=showAbout;
  $("createClear").onclick=()=>{
    if(!createState.nodes.length)return;
    if(!confirm("Очистить предпросмотр? Файлы ещё не созданы."))return;
    createState.nodes=[];createState.edges=[];createState.seq=1;
    $("createResult").style.display="none";
    renderCreateGraph();schedulePersist();
  };
  $("createSubmit").onclick=submitCreate;
  initSearch();
  updateDiagBadge();
  updateBriefBadge();
  refreshBriefStatus();
  setLive(false);
  startLive();
  window.addEventListener("beforeunload",ev=>{
    persistNow();
    if(formDirty&&selectedNode){
      ev.preventDefault();ev.returnValue="";
      try{
        const n=byId[selectedNode];
        const desc=$("fDesc")?$("fDesc").value:n.description;
        const acc=$("fAcc")?$("fAcc").value.split("\n").map(s=>s.trim()).filter(Boolean):n.acceptance;
        const dom=$("fDomain")?$("fDomain").value:(n.kind==="endpoint"?n.domain:"");
        fetch("/api/metadata/node",{method:"POST",keepalive:true,headers:{"Content-Type":"application/json","X-Studio-Token":studioToken()},body:JSON.stringify({node_id:n.id,description:desc,acceptance:acc,domain:dom,base_hash:n.file_hash||""})});
      }catch(_){}
    }
  });
  document.addEventListener("click",schedulePersist);
  document.addEventListener("change",schedulePersist);
  const initial=location.hash.replace(/^#/,"");
  setTab(["map","scenario","create"].includes(initial)?initial:tab);
}
boot();

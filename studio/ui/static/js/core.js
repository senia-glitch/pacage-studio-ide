import {api as apiRequest, onRevision} from "./api.js";
import {enableNodeDrag as enableNodeDragImpl, enableZoneDrag as enableZoneDragImpl} from "./drag.js";
import {EDITORS, buildEditorUrl} from "./editor.js";
import {drawEdges as drawEdgesImpl} from "./edges.js";
import {areaSize as geometryAreaSize, proposedOverlap as geometryOverlap, zoneOverlaps as geometryZoneOverlaps} from "./geometry.js";
import {matchesAll, searchNodes} from "./search.js";
import {nextState, saveLabel} from "./savestate.js";
import {createStorage, storageKey} from "./storage.js";
import {anchorOf, isTodo, placeNode, planPath, todoBadge} from "./todo.js";
import {$, esc, toast} from "./util.js";
import {applyTransform as applyTransformImpl, revealElement} from "./viewport.js";

"use strict";

// Общее состояние UI. Меняется только через действия пользователя и live-скан.
export const S = {
  G: null,
  byId: {},
  tab: "map",
  selectedScenario: null,
  selectedNode: null,
  hideMeta: false,
  hideNested: false,
  scenView: {x: 30, y: 30, k: 1},
  mapView: {x: 30, y: 30, k: 1},
  createView: {x: 30, y: 30, k: 1},
  scenBoxes: {},
  mapBoxes: {},
  mapPos: new Map(),
  scenPos: new Map(),
  nestedPos: new Map(),
  listOpen: new Map(),
  incompleteZoneMoved: false,
  mapFastbase: {x: 40, y: 40},
  mapOffpath: {x: 40, y: 420},
  mapMethods: new Map(),
  scenZonePos: new Map(),
  justDraggedAt: 0,
  todoHighlight: false,
  createState: {nodes: [], edges: [], seq: 1, zones: {}},
  createConnect: null,
  studioRevision: 0,
  liveFailures: 0,
  liveStarted: false,
  dragging: false,
  domainFilter: new Set(),
  uiStorage: null,
  saveState: "idle",
  formDirty: false,
  persistTimer: null,
  lastSearchQuery: "",
  editorId: "vscode",
  studioInfo: null,
  briefReport: null,
  briefLast: null,
  briefStatusTimer: null,
  briefPreview: null,
};

export const hooks = {
  renderMap() {},
  renderScenario() {},
  renderCreate() {},
  renderInspector() {},
  updateBriefBadge() {},
  scheduleBriefStatus() {},
  updateDiagBadge() {},
  saveMetadata: async () => {},
};

const api = apiRequest;
try {
  S.editorId = localStorage.getItem("studio:editor") || "vscode";
} catch (_) {}
onRevision((value) => { S.studioRevision = Math.max(S.studioRevision, value); });

"use strict";
const KIND_NAMES={endpoint:"эндпоинт",scenario:"сценарий",io:"io",model:"модель",test:"тест",nested:"вложенный"};
const ROLE_NAMES={schema:"схема",dependency:"зависимость",exception:"исключение",settings:"настройки",utils:"утилита",other:"прочее",method:"метод",function:"функция",module:"модуль"};
const KIND_COLORS={endpoint:"#5aa0ff",scenario:"#e8833a",io:"#7fc97f",model:"#bb9af7",test:"#c9a86a",nested:"#8f8f98"};
const EDGE_COLORS={dispatch:"#5aa0ff",scenario_call:"#e8833a",port_call:"#7fc97f",table:"#bb9af7",tests:"#c9a86a",uses:"#9a9aa2",depends:"#9a9aa2"};
const EDGE_NAMES={dispatch:"вызов сценария",scenario_call:"вызов сценария",port_call:"порт БД",table:"таблица",tests:"тест",uses:"использует",depends:"зависит"};
const METHOD_COLORS={GET:"#5aa0ff",POST:"#7fc97f",PUT:"#e8833a",PATCH:"#bb9af7",DELETE:"#ff5d5d","ДРУГОЙ":"#9a9aa2","Метод не указан":"#9a9aa2"};
const NO_DOMAIN="Без домена";
const NO_METHOD="Метод не указан";
const CARD_W=280,GAPY=24,PAD=30;
const CREATE_KINDS=["endpoint","scenario","io","model","test"];
const CREATE_LAYER_X={endpoint:40,scenario:400,io:760,model:1120,test:1480};

// Общие константы и чистые помощники.


function sameAnchor(left,right){return String(left||"").replace(/#\d+$/,"")===String(right||"").replace(/#\d+$/,"")}
function migrateTodoState(previous,current){
  if(!previous||!previous.nodes||!current||!current.nodes)return;
  const previousSelected=S.selectedNode,previousScenario=S.selectedScenario;
  const oldById={};(previous.nodes||[]).forEach(n=>oldById[n.id]=n);
  const newByAnchor={};(current.nodes||[]).forEach(n=>{
    if(n.planned)return;
    const key=`${n.kind}|${anchorOf(n)}`;
    if(!newByAnchor[key])newByAnchor[key]=n;
  });
  for(const old of previous.nodes||[]){
    if(!old.planned||S.byId[old.id])continue;
    const key=`${old.kind}|${anchorOf(old)}`;
    let match=newByAnchor[key];
    if(!match&&old.kind==="io"){
      match=(current.nodes||[]).find(n=>!n.planned&&n.kind==="io"&&sameAnchor(anchorOf(n),anchorOf(old)));
    }
    if(!match)continue;
    if(previousSelected===old.id)S.selectedNode=match.id;
    if(previousScenario===old.id)S.selectedScenario=match.id;
    if(S.mapPos.has(old.id))S.mapPos.set(match.id,S.mapPos.get(old.id));
    for(const [posKey,value] of [...scenPos.entries()]){
      const parts=posKey.split("|");
      if(parts[parts.length-1]===old.id){
        S.scenPos.set([...parts.slice(0,-1),match.id].join("|"),value);
      }
    }
    for(const [posKey,value] of [...S.nestedPos.entries()]){
      if(posKey.includes(`nested:${old.id}:`)){
        S.nestedPos.set(posKey.replace(`nested:${old.id}:`,`nested:${match.id}:`),value);
      }
    }
  }
}
function applyGraph(g){
  const previous=S.G;
  S.G=g||{};S.byId={};(S.G.nodes||[]).forEach(n=>S.byId[n.id]=n);
  migrateTodoState(previous,S.G);
  if(S.selectedScenario&&!S.G.scenario_paths?.[S.selectedScenario])S.selectedScenario=null;
  if(S.selectedNode&&!S.byId[S.selectedNode])S.selectedNode=null;
  hooks.updateBriefBadge();
  hooks.scheduleBriefStatus();
}
function methodOfEndpoint(n){const m=String((n.details||{}).http_method||"").toUpperCase();return m||NO_METHOD}
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
  if(n.kind==="nested")return [ROLE_NAMES[d.role]||d.role||"",...(d.symbols||[])].filter(Boolean).join(" · ");
  return d.class_name||d.name||"";
}
function notesOf(n){return Array.isArray(n&&n.notes)?n.notes:[]}
function nestedOf(parentId){
  return (S.G&&S.G.edges||[]).filter(e=>e.kind==="uses"&&e.from_node===parentId)
    .map(e=>S.byId[e.to_node]).filter(n=>n&&n.kind==="nested");
}
function nestedListHtml(n){
  if(S.hideNested)return "";
  const items=nestedOf(n.id);
  if(!items.length)return "";
  return `<div class="nested-list"><div class="lbl">вложенные</div>`+
    items.map(item=>`<button type="button" class="nested-item" data-nested="${esc(item.id)}" title="${esc(item.file_path+":"+item.line)}">${esc(item.label)}</button>`).join("")+
    `</div>`;
}
function notesHtml(n){
  const notes=notesOf(n);
  if(!notes.length)return "";
  return `<div class="yellow-note" title="${esc(notes.map(x=>x.message).join("; "))}">`+
    notes.map(x=>esc(x.message)).join("<br>")+`</div>`;
}
function todoBadgeHtml(n){
  const badge=todoBadge(n);if(!badge)return "";
  const extra=badge.variant==="нет описания"?" no-desc":"";
  return `<span class="chip todo${extra}" title="${esc(badge.text)}">${esc(badge.text)}</span>`;
}
function planLineHtml(n){
  const path=planPath(n);
  return path?`<div class="plan-line">план: ${esc(path)}</div>`:"";
}
function todoDownstreamHtml(n){
  const count=Number(n.todo_downstream||(n.details||{}).todo_downstream||0);
  if(n.kind!=="endpoint"||!count)return "";
  return `<button type="button" class="chip todo-chain" title="Открыть цепочку с планируемыми узлами">в цепочке TODO: ${count}</button>`;
}
function nodeTodoClass(n){return isTodo(n)?" node--todo":""}
function domainOfNode(n){return String(n.domain||"").trim()||NO_DOMAIN}
function cardHtml(n,opts={}){
  const c=opts.method?METHOD_COLORS[opts.method]:KIND_COLORS[n.kind]||"#888";
  const extra=(n.details&&n.details.extra_routes||[]).length;
  const yellow=notesOf(n).length>0;
  let h=`<div class="hd"><span class="grip" title="перетащить">⠿</span><span class="chip">${esc(KIND_NAMES[n.kind]||n.kind)}</span>${todoBadgeHtml(n)}${yellow?`<span class="chip warn" title="Почему жёлтый — в инспекторе">неточно</span>`:""}${extra?`<span class="chip" title="Ещё маршруты: ${extra}">+${extra}</span>`:""}<span class="t">${esc(n.label)}</span><button class="open-editor" type="button" title="Открыть в VS Code" aria-label="Открыть в VS Code">↗</button></div>`;
  h+=planLineHtml(n);
  h+=`<div class="mt">${esc(nodeMeta(n))}</div>`;
  if(opts.map&&n.kind==="endpoint")h+=nestedListHtml(n);
  if(!S.hideMeta){
    h+=notesHtml(n);
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
  const cls=`card k-${n.kind}${n.planned?" planned":""}${yellow?" node--yellow":""}${nodeTodoClass(n)}${opts.method?" method-"+opts.method.toLowerCase():""}${opts.nested?" nested":""}${S.selectedNode===n.id?" sel":""}`;
  return {cls,html:h,c};
}
function nestedLayoutKey(scope,nid){return `${scope}|${nid}`}

async function onCardClick(id){
  if(Date.now()-S.justDraggedAt<250)return;
  const n=S.byId[id];if(!n)return;
  await flushPending();
  if(S.tab==="map"&&n.kind==="endpoint"){openEndpointScenario(id);return}
  S.selectedNode=id;hooks.renderScenario();hooks.renderInspector();schedulePersist();
}
async function openEndpointScenario(endpointId){
  await flushPending();
  const edge=(S.G.edges||[]).find(e=>e.kind==="dispatch"&&e.from_node===endpointId);
  if(!edge){toast("У эндпоинта нет сценария (dispatch не найден)");return}
  S.selectedScenario=edge.to_node;S.selectedNode=endpointId;setTab("scenario");
}

/* ---------- вкладки ---------- */
async function setTab(name){
  await flushPending();
  S.tab=name;
  document.querySelectorAll(".tabbtn").forEach(b=>b.classList.toggle("on",b.dataset.tab===name));
  document.querySelectorAll(".tab").forEach(s=>s.classList.toggle("on",s.id==="tab-"+name));
  if(name==="map")hooks.renderMap();
  else if(name==="scenario")hooks.renderScenario();
  else hooks.renderCreate();
  schedulePersist();
}

/* ---------- viewport ---------- */
function applyTransform(viewId,view){
  applyTransformImpl(viewId,view,updateCreateZoneScales);
}
function updateCreateZoneScales(){
  const stage=$("createStage");if(!stage)return;
  const k=S.createView.k||1;
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
  start:()=>{S.dragging=true},
  end:moved=>{S.dragging=false;if(moved)S.justDraggedAt=Date.now()},
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

function nodeAbsPath(n){
  const root=String((S.G&&S.G.project_root)||"").replace(/[\\/]+$/,"");
  return root?root+"/"+n.file_path:n.file_path;
}
function openEditorForNode(n){
  if(!n)return;
  const line=n.planned?1:(n.line||1);
  const url=buildEditorUrl("vscode",nodeAbsPath(n),n.file_path,line);
  const anchor=document.createElement("a");
  anchor.href=url;anchor.style.display="none";document.body.appendChild(anchor);anchor.click();anchor.remove();
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
  select.value=S.editorId;
  select.onchange=()=>{
    S.editorId=select.value;
    try{localStorage.setItem("studio:editor",S.editorId)}catch(_){ }
    schedulePersist();
  };
}
function setSaveState(next){
  S.saveState=nextState(S.saveState,next);
  const foot=$("inspFoot");
  if(foot&&S.saveState!=="idle")foot.textContent=saveLabel(S.saveState);
}
function currentPositions(){
  const dump=map=>Object.fromEntries([...map.entries()]);
  return{map:dump(S.mapPos),scen:dump(S.scenPos),nested:dump(S.nestedPos),zones:dump(S.scenZonePos)};
}
function uiSnapshot(){
  return{
    tab:S.tab,selectedScenario:S.selectedScenario,selectedNode:S.selectedNode,hideMeta:S.hideMeta,hideNested:S.hideNested,todoHighlight:S.todoHighlight,editor:S.editorId,
    views:{map:S.mapView,scen:S.scenView,create:S.createView},
    positions:currentPositions(),
    listOpen:Object.fromEntries(S.listOpen),
    createDraft:{nodes:S.createState.nodes,edges:S.createState.edges,seq:S.createState.seq,zones:S.createState.zones},
  };
}
function persistNow(){
  if(!S.uiStorage||!S.G)return;
  S.uiStorage.save(uiSnapshot());
}
function schedulePersist(){
  clearTimeout(S.persistTimer);
  S.persistTimer=setTimeout(persistNow,300);
}
function restoreUiState(saved){
  if(!saved)return;
  if(["map","scenario","create"].includes(saved.tab))S.tab=saved.tab;
  if(S.byId[saved.selectedNode])S.selectedNode=saved.selectedNode;
  if((S.G.scenario_paths||{})[saved.selectedScenario])S.selectedScenario=saved.selectedScenario;
  S.hideMeta=!!saved.hideMeta;
  S.hideNested=!!saved.hideNested;
  S.todoHighlight=!!saved.todoHighlight;
  applyTodoHighlight();
  if(EDITORS.some(e=>e.id===saved.editor))S.editorId=saved.editor;
  for(const key of ["map","scen","create"]){
    const view=saved.views&&saved.views[key];
    if(view&&Number.isFinite(view.x)&&Number.isFinite(view.y)&&Number.isFinite(view.k)){
      const target=key==="map"?S.mapView:key==="scen"?S.scenView:S.createView;
      target.x=view.x;target.y=view.y;target.k=view.k;
    }
  }
  const ids=new Set(Object.keys(S.byId));
  const scopes=new Set(["incomplete",...(S.G.scenarios||[])]);
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
  restored(positions.map).forEach((v,k)=>S.mapPos.set(k,v));
  restored(positions.scen).forEach((v,k)=>S.scenPos.set(k,v));
  restored(positions.nested).forEach((v,k)=>S.nestedPos.set(k,v));
  Object.entries(positions.zones||{}).forEach(([k,v])=>{
    if(v&&Number.isFinite(v.x)&&Number.isFinite(v.y))S.scenZonePos.set(k,{x:v.x,y:v.y});
  });
  Object.entries(saved.listOpen||{}).forEach(([key,value])=>S.listOpen.set(key,!!value));
  const draft=saved.createDraft;
  if(draft&&Array.isArray(draft.nodes)&&draft.nodes.length){
    S.createState.nodes=draft.nodes;S.createState.edges=Array.isArray(draft.edges)?draft.edges:[];S.createState.seq=draft.seq||1;S.createState.zones=draft.zones&&typeof draft.zones==="object"?draft.zones:{};
    toast(`Восстановлен черновик (${draft.nodes.length} узлов)`);
  }
}
function initUiStorage(){
  try{
    S.uiStorage=createStorage(window.localStorage,storageKey((S.G&&S.G.project_root)||location.origin));
    restoreUiState(S.uiStorage.load());
    if($("hideMeta"))$("hideMeta").checked=S.hideMeta;
    if($("hideNested"))$("hideNested").checked=S.hideNested;
  }catch(_){S.uiStorage=null}
}
function setLive(online){
  const dot=$("liveDot");if(!dot)return;
  dot.classList.toggle("on",online);
  dot.classList.toggle("off",!online);
  dot.title=online?"live · обновлено "+new Date().toLocaleTimeString(): "нет связи со студией — перезапустите studio";
  dot.textContent=online?"● live":"● нет связи";
}
function rerenderActive(){
  if(S.tab==="map")hooks.renderMap();
  else if(S.tab==="scenario")hooks.renderScenario();
  else hooks.renderCreate();
  hooks.updateDiagBadge();
}
async function refreshLiveGraph(){
  if(S.dragging){setTimeout(refreshLiveGraph,400);return}
  const previous=S.selectedNode?S.byId[S.selectedNode]:null;
  const graph=await api("/api/graph");
  applyGraph(graph);
  if(previous&&!S.selectedNode)toast("Узел пропал из кода — выбор снят");
  if(!S.formDirty)rerenderActive();
  else if(S.tab!=="scenario")rerenderActive();
  schedulePersist();
}
async function liveLoop(){
  while(S.liveStarted){
    try{
      const data=await api("/api/wait?rev="+S.studioRevision);
      S.liveFailures=0;setLive(true);
      if(data&&Number(data.revision)>S.studioRevision){
        S.studioRevision=Number(data.revision);
        await refreshLiveGraph();
      }
    }catch(_e){
      S.liveFailures+=1;setLive(false);
      await new Promise(done=>setTimeout(done,Math.min(5000,800*S.liveFailures)));
    }
  }
}
function startLive(){
  if(S.liveStarted)return;
  S.liveStarted=true;liveLoop();
}
function renderDomainChips(){
  const box=$("domainChips");if(!box)return;
  const names=[...new Set((S.G.nodes||[]).filter(n=>n.kind==="endpoint").flatMap(n=>String(n.domain||NO_DOMAIN).split(",").map(s=>s.trim()).filter(Boolean)))].sort();
  box.innerHTML=names.map(name=>`<button type="button" class="chip${S.domainFilter.has(name)?" acc":""}" data-domain="${esc(name)}">${esc(name)}</button>`).join("");
  box.querySelectorAll("button").forEach(button=>{
    button.onclick=()=>{
      const name=button.dataset.domain;
      if(S.domainFilter.has(name))S.domainFilter.delete(name);else S.domainFilter.add(name);
      hooks.renderMap();
    };
  });
}
function showAbout(){
  if(!S.studioInfo){toast("Нет данных о проекте");return}
  const env=S.studioInfo.env||{},counts=env.counts||{};
  modal("О проекте",
    `<div class="kv"><span>корень</span><b>${esc(S.studioInfo.project_root)}</b></div>`+
    `<div class="kv"><span>версия</span><b>${esc(S.studioInfo.version)}</b></div>`+
    `<div class="kv"><span>ревизия</span><b>${esc(S.studioInfo.revision)} · ${esc(S.studioInfo.scanning?"скан идёт":"готово")}</b></div>`+
    `<div class="kv"><span>скан</span><b>${esc(S.studioInfo.generated_at||"")}</b></div>`+
    `<div class="kv"><span>счётчики</span><b>${esc(Object.entries(counts).map(([k,v])=>k+": "+v).join(" · "))}</b></div>`+
    `<div class="kv"><span>роуты</span><b>${esc(env.routes_package||"")}</b></div>`+
    `<div class="kv"><span>сценарии</span><b>${esc(env.core_discover||"")}</b></div>`+
    `<div class="kv"><span>модели</span><b>${esc(env.models||"")}</b></div>`,
    [{label:"Закрыть",cls:"primary",action:()=>{}}]);
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
function toastAction(message,label,action){
  const t=$("toast");if(!t)return;
  t.innerHTML=`<span>${esc(message)}</span> <button type="button">${esc(label)}</button>`;
  t.style.display="block";clearTimeout(t._h);
  const button=t.querySelector("button");
  if(button)button.onclick=()=>{t.style.display="none";action()};
  t._h=setTimeout(()=>{t.style.display="none"},8000);
}
async function flushPending(){
  if(!S.formDirty||!S.selectedNode)return;
  await hooks.saveMetadata();
}
function initSearch(){
  const box=$("searchBox");if(!box)return;
  const update=()=>{
    S.lastSearchQuery=box.value.trim();
    const all=(S.G.nodes||[]).filter(n=>n.kind==="endpoint");
    const found=all.filter(n=>!S.lastSearchQuery||matchesAll(S.lastSearchQuery,[n.label,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description,n.details&&n.details.function_name]));
    const count=$("searchCount");if(count)count.textContent=S.lastSearchQuery?`найдено ${found.length} из ${all.length}`:"";
    if(S.tab==="map")hooks.renderMap();else if(S.tab==="scenario")hooks.renderScenario();
  };
  box.addEventListener("input",update);
  box.addEventListener("keydown",ev=>{
    if(ev.key!=="Enter")return;
    const all=(S.G.nodes||[]).filter(n=>n.kind==="endpoint");
    const first=all.find(n=>matchesAll(S.lastSearchQuery,[n.label,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description]));
    if(first)openEndpointScenario(first.id);
  });
}
function openPalette(){
  const nodes=(S.G&&S.G.nodes)||[];
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
      S.selectedScenario=node.id;S.selectedNode=node.id;setTab("scenario");return;
    }
    const owner=(S.G.scenarios||[]).find(sid=>{
      const path=S.G.scenario_paths&&S.G.scenario_paths[sid];
      return path&&(path.io_calls||[]).includes(node.id)||path&&(path.models||[]).includes(node.id)||path&&(path.tests||[]).includes(node.id);
    });
    if(owner){S.selectedScenario=owner;S.selectedNode=node.id;setTab("scenario");hooks.renderScenario();hooks.renderInspector()}
    else{setTab("scenario");S.selectedNode=node.id;S.selectedScenario=null;hooks.renderScenario();toast("Узел в разделе «Неполные»")}
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


function anchorText(n){
  const d=n.details||{};
  if(n.kind==="endpoint")return d.function_name||"";
  if(n.kind==="scenario"||n.kind==="model")return d.class_name||"";
  if(n.kind==="test")return d.test_name||"";
  if(n.kind==="io")return n.planned?`${d.scenario_class}._db.${d.method}`:`${d.scenario_class}._db.${d.method}#${d.ordinal||1}`;
  if(n.kind==="nested")return d.name||n.label||"";
  return "";
}


export {
    $, esc, toast,
    KIND_NAMES,
    ROLE_NAMES,
    EDGE_NAMES,
    METHOD_COLORS,
    NO_DOMAIN,
    NO_METHOD,
    CREATE_KINDS,
    CREATE_LAYER_X,
    applyGraph,
    methodOfEndpoint,
    urlOfEndpoint,
    nodeMeta,
    notesOf,
    nestedOf,
    nestedListHtml,
    todoBadgeHtml,
    planLineHtml,
    todoDownstreamHtml,
    nodeTodoClass,
    domainOfNode,
    cardHtml,
    nestedLayoutKey,
    onCardClick,
    openEndpointScenario,
    setTab,
    applyTransform,
    updateCreateZoneScales,
    enableZoneDrag,
    enableNodeDrag,
    drawEdges,
    areaSize,
    proposedOverlap,
    zoneOverlaps,
    styleRect,
    openEditorForNode,
    copyNodeLocation,
    initEditorChoice,
    setSaveState,
    persistNow,
    schedulePersist,
    initUiStorage,
    setLive,
    startLive,
    renderDomainChips,
    showAbout,
    modal,
    toastAction,
    flushPending,
    initSearch,
    openPalette,
    anchorText,
};












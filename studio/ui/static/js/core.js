import {api as apiRequest, onRevision} from "./api.js";
import {enableNodeDrag as enableNodeDragImpl} from "./drag.js";
import {EDITORS, buildEditorUrl} from "./editor.js";
import {drawEdges as drawEdgesImpl} from "./edges.js";
import {matchesAll, searchNodes} from "./search.js";
import {createStorage, storageKey} from "./storage.js";
import {anchorOf} from "./anchors.js";
import {$, esc, toast} from "./util.js";
import {applyTransform as applyTransformImpl} from "./viewport.js";

"use strict";

// Общее состояние UI. Меняется только через действия пользователя и live-скан.
export const S = {
  G: null,
  byId: {},
  tab: "map",
  selectedNode: null,
  mapMode: "fact",
  mapViewNodes: {},
  inspectorOpen: true,
  inspectorWidth: 340,
  treeOpen: true,
  treeWidth: 280,
  treeMode: "all",
  treeCollapsed: new Set(),
  treeCollapsedInit: false,
  treeFreeInit: false,
  hideMeta: false,
  todoHighlight: false,
  mapView: {x: 30, y: 30, k: 1},
  mapBoxes: {},
  mapPos: new Map(),
  mapH: new Map(),
  justDraggedAt: 0,
  studioRevision: 0,
  liveFailures: 0,
  liveStarted: false,
  dragging: false,
  uiStorage: null,
  persistTimer: null,
  lastSearchQuery: "",
  editorId: "vscode",
  studioInfo: null,
  briefReport: null,
  briefLast: null,
  briefStatusTimer: null,
  briefPreview: null,
  studioAux: null,
};

export const hooks = {
  refreshAux() {},
  renderMap() {},
  renderIdeas() {},
  renderInspector() {},
  updateBriefBadge() {},
  scheduleBriefStatus() {},
  updateDiagBadge() {},
};

const api = apiRequest;
try {
  S.editorId = localStorage.getItem("studio:editor") || "vscode";
} catch (_) {}
onRevision((value) => { S.studioRevision = Math.max(S.studioRevision, value); });

"use strict";
const KIND_NAMES={endpoint:"эндпоинт",scenario:"сценарий",io:"io",model:"модель",test:"тест",nested:"вложенный"};
const ROLE_NAMES={schema:"схема",dependency:"зависимость",exception:"исключение",settings:"настройки",utils:"утилита",other:"прочее",method:"метод",function:"функция",module:"модуль"};
const EDGE_COLORS={dispatch:"#5aa0ff",scenario_call:"#e8833a",port_call:"#7fc97f",table:"#bb9af7",tests:"#c9a86a",uses:"#9a9aa2",depends:"#9a9aa2"};
const EDGE_NAMES={dispatch:"вызов сценария",scenario_call:"вызов сценария",port_call:"порт БД",table:"таблица",tests:"тест",uses:"использует",depends:"зависит"};
const NO_METHOD="Метод не указан";

// Общие константы и чистые помощники.


function sameAnchor(left,right){return String(left||"").replace(/#\d+$/,"")===String(right||"").replace(/#\d+$/,"")}
function migrateTodoState(previous,current){
  if(!previous||!previous.nodes||!current||!current.nodes)return;
  const previousSelected=S.selectedNode;
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
    if(S.mapPos.has(old.id))S.mapPos.set(match.id,S.mapPos.get(old.id));
  }
}
function applyGraph(g){
  const previous=S.G;
  S.G=g||{};S.byId={};(S.G.nodes||[]).forEach(n=>S.byId[n.id]=n);
  migrateTodoState(previous,S.G);
  if(S.selectedNode&&!S.byId[S.selectedNode]&&!String(S.selectedNode).startsWith("@")&&S.selectedNode!=="__new_domain__")S.selectedNode=null;
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
/* ---------- вкладки ---------- */
async function setTab(name){
  if(name==="create"||name==="scenario")name="map";
  if(!(await flushPending()))return;
  S.tab=name;
  document.body.dataset.activeTab=name;
  document.querySelectorAll(".tabbtn").forEach(b=>{const active=b.dataset.tab===name;b.classList.toggle("on",active);b.setAttribute("aria-selected",String(active))});
  document.querySelectorAll(".tab").forEach(s=>s.classList.toggle("on",s.id==="tab-"+(name==="ideas"?"ideas":name)));
  const search=$("searchBox");if(search){search.placeholder=name==="ideas"?"Поиск по идеям и сценариям":"Поиск — метод, URL, имя, домен";search.setAttribute("aria-label",name==="ideas"?"Поиск по идеям и сценариям":"Поиск по проекту")}
  if(name==="map"){hooks.renderMap();hooks.renderInspector()}
  else if(name==="settings"){if(hooks.renderSettings)hooks.renderSettings()}
  else if(name==="frontend"){hooks.renderFrontend&&hooks.renderFrontend()}
  else hooks.renderIdeas();
  schedulePersist();
  return true;
}

/* ---------- viewport ---------- */
function applyTransform(viewId,view){
  applyTransformImpl(viewId,view);
}

/* ---------- перемещение узлов ---------- */
const dragHooks={
  start:()=>{S.dragging=true},
  end:moved=>{S.dragging=false;if(moved)S.justDraggedAt=Date.now()},
  persist:schedulePersist,
};
function enableNodeDrag(card,scope,key,x,y,view,onMove,onEnd,onCancel){
  enableNodeDragImpl(card,view,onMove,onEnd,onCancel?{...dragHooks,cancel:onCancel}:dragHooks);
}
function drawEdges(svg,boxes,edges){
  drawEdgesImpl(svg,boxes,edges,EDGE_COLORS,EDGE_NAMES,esc);
}

/* ---------- Карта ---------- */
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
function currentPositions(map=S.mapPos){
  return{map:Object.fromEntries([...map.entries()])};
}
function uiSnapshot(){
  return{
    tab:S.tab,selectedNode:S.selectedNode,hideMeta:S.hideMeta,todoHighlight:S.todoHighlight,editor:S.editorId,mapMode:S.mapMode,inspectorOpen:S.inspectorOpen,inspectorWidth:S.inspectorWidth,
    views:{map:S.mapView},
    positions:currentPositions(),
    nodeHeights:currentPositions(S.mapH),
    tree:{open:S.treeOpen,width:Math.round(S.treeWidth),mode:S.treeMode,collapsed:[...S.treeCollapsed],init:S.treeCollapsedInit,freeInit:S.treeFreeInit},
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
  if(["map","ideas","frontend"].includes(saved.tab))S.tab=saved.tab;else S.tab="map";
  if(saved.mapMode==="fact"||saved.mapMode==="plan")S.mapMode=saved.mapMode;
  if(typeof saved.inspectorOpen==="boolean")S.inspectorOpen=saved.inspectorOpen;
  if(Number.isFinite(saved.inspectorWidth))S.inspectorWidth=Math.max(280,Math.min(520,saved.inspectorWidth));
  if(S.byId[saved.selectedNode]||String(saved.selectedNode||"").startsWith("@")||saved.selectedNode==="__new_domain__")S.selectedNode=saved.selectedNode;
  S.hideMeta=!!saved.hideMeta;
  S.todoHighlight=!!saved.todoHighlight;
  document.body.classList.toggle("todo-highlight",S.todoHighlight);
  if(EDITORS.some(e=>e.id===saved.editor))S.editorId=saved.editor;
  for(const key of ["map"]){
    const view=saved.views&&saved.views[key];
    if(view&&Number.isFinite(view.x)&&Number.isFinite(view.y)&&Number.isFinite(view.k)){
      const target=S.mapView;
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
      if(!ids.has(id)&&!id.startsWith("@branch:")&&!id.startsWith("@method:")&&!id.startsWith("@domain:")&&!id.startsWith("@domain-plan:")&&!id.startsWith("@domain-change:")&&!scopes.has(scope)&&!scope.includes("incomplete"))continue;
      out.set(key,{x:value.x,y:value.y});
    }
    return out;
  };
  const positions=(saved.positions||{});
  restored(positions.map).forEach((v,k)=>S.mapPos.set(k,v));
  for(const [key,value] of Object.entries(saved.nodeHeights||{})){
    if(!Number.isFinite(value))continue;
    S.mapH.set(key,Math.max(126,Math.min(460,Math.round(value))));
  }
  const tree=saved.tree&&typeof saved.tree==="object"?saved.tree:null;
  if(tree){
    if(typeof tree.open==="boolean")S.treeOpen=tree.open;
    if(Number.isFinite(tree.width))S.treeWidth=Math.max(220,Math.min(520,tree.width));
    if(tree.mode==="all"||tree.mode==="fact"||tree.mode==="plan")S.treeMode=tree.mode;
    if(typeof tree.freeInit==="boolean")S.treeFreeInit=tree.freeInit;
    if(Array.isArray(tree.collapsed)){
      for(const id of tree.collapsed)if(String(id))S.treeCollapsed.add(String(id));
      S.treeCollapsedInit=!!tree.init;
    }
  }
}
function initUiStorage(){
  try{
    S.uiStorage=createStorage(window.localStorage,storageKey((S.G&&S.G.project_root)||location.origin));
    restoreUiState(S.uiStorage.load());
    if($("hideMeta"))$("hideMeta").checked=S.hideMeta;
  }catch(_){S.uiStorage=null}
}
function setLive(online){
  const dot=$("liveDot");if(!dot)return;
  dot.classList.toggle("on",online);
  dot.classList.toggle("off",!online);
  dot.title=online?"live · обновлено "+new Date().toLocaleTimeString(): "нет связи со студией — перезапустите studio";
  dot.textContent=online?"● live":"● нет связи";
  const banner=$("offlineBanner");
  if(banner)banner.hidden=online;
}
function rerenderActive(){
  if(S.tab==="map"){hooks.renderMap();hooks.renderInspector()}
  else if(S.tab==="frontend"){hooks.renderFrontend&&hooks.renderFrontend()}
  else hooks.renderIdeas();
  hooks.updateDiagBadge();
}
async function refreshLiveGraph(){
  if(S.dragging){setTimeout(refreshLiveGraph,400);return}
  const previous=S.selectedNode?S.byId[S.selectedNode]:null;
  const graph=await api("/api/graph");
  applyGraph(graph);
  if(previous&&!S.selectedNode)toast("Узел пропал из кода — выбор снят");
  rerenderActive();
  schedulePersist();
}
async function liveLoop(){
  while(S.liveStarted){
    try{
      const data=await api("/api/wait?rev="+S.studioRevision+"&aux="+(S.studioAux===null?-1:S.studioAux));
      S.liveFailures=0;setLive(true);
      if(data&&data.aux!==undefined){
        const aux=Number(data.aux);
        if(S.studioAux===null)S.studioAux=aux;
        else if(aux>S.studioAux){S.studioAux=aux;try{hooks.refreshAux()}catch(e){console.error("refreshAux:",e)}}
      }
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
async function flushPending(){return true}
function initSearch(){
  const box=$("searchBox");if(!box)return;
  const update=()=>{
    S.lastSearchQuery=box.value.trim();
    const all=S.G.nodes||[];
    const found=all.filter(n=>!S.lastSearchQuery||matchesAll(S.lastSearchQuery,[n.label,n.kind,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description,n.file_path,n.details&&n.details.function_name]));
    const count=$("searchCount");if(count)count.textContent=S.lastSearchQuery?`найдено ${found.length} из ${all.length} узлов`:"";
    if(S.tab==="map")hooks.renderMap();else hooks.renderIdeas();
  };
  box.addEventListener("input",update);
  box.addEventListener("keydown",ev=>{
    if(ev.key!=="Enter"||S.tab==="ideas")return;
    const first=(S.G.nodes||[]).find(n=>matchesAll(S.lastSearchQuery,[n.label,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description,n.file_path]));
    if(first){S.selectedNode=first.id;hooks.renderMap();hooks.renderInspector()}
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
    S.selectedNode=node.id;setTab("map");hooks.renderInspector();
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
    EDGE_COLORS,
    EDGE_NAMES,
    NO_METHOD,
    applyGraph,
    methodOfEndpoint,
    urlOfEndpoint,
    nodeMeta,
    setTab,
    applyTransform,
    enableNodeDrag,
    drawEdges,
    openEditorForNode,
    initEditorChoice,
    persistNow,
    schedulePersist,
    initUiStorage,
    setLive,
    startLive,
    showAbout,
    modal,
    flushPending,
    initSearch,
    openPalette,
    anchorText,
};












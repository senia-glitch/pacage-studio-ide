import {api, studioToken} from "./api.js";
import {
  $, S, applyGraph, applyTransform, flushPending, hooks, initEditorChoice, initSearch,
  initUiStorage, modal, openEditorForNode, openPalette, persistNow, schedulePersist, setTab, showAbout,
  setLive, startLive, toast,
} from "./core.js";
import {refreshBriefStatus, scheduleBriefStatus, updateBriefBadge, initTodoChip} from "./briefview.js";
import {renderCreate} from "./createview.js";
import {openDiagnostics, updateDiagBadge} from "./diagnosticsview.js";
import {renderInspector, saveMetadata} from "./inspectorview.js";
import {renderMap} from "./mapview.js";
import {renderScenario} from "./scenarioview.js";
import {bindViewport, fitView} from "./viewport.js";

hooks.renderMap = renderMap;
hooks.renderScenario = renderScenario;
hooks.renderCreate = renderCreate;
hooks.renderInspector = renderInspector;
hooks.updateBriefBadge = updateBriefBadge;
hooks.scheduleBriefStatus = scheduleBriefStatus;
hooks.updateDiagBadge = updateDiagBadge;
hooks.saveMetadata = saveMetadata;

function activeView(){
  if(S.tab==="scenario")return{view:S.scenView,id:"scenView",canvas:$("scenCanvas")};
  return{view:S.mapView,id:"mapView",canvas:$("mapCanvas")};
}
async function boot(){
  try{
    applyGraph(await api("/api/graph"));
  }catch(e){toast("не удалось загрузить граф: "+e.message);S.G={nodes:[],edges:[],scenarios:[],scenario_paths:{},diagnostics:[]}}
  try{
    S.studioInfo=await api("/api/state");
    S.studioRevision=Number(S.studioInfo.revision)||S.studioRevision;
    document.title="studio · "+String(S.studioInfo.project_root||"").replace(/[\\/]+$/,"").split(/[\\/]/).pop();
  }catch(_){ }
  initUiStorage();
  initEditorChoice();
  document.querySelectorAll(".tabbtn").forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
  bindViewport("mapCanvas",S.mapView,()=>applyTransform("mapView",S.mapView));
  bindViewport("scenCanvas",S.scenView,()=>applyTransform("scenView",S.scenView));
  $("scenCanvas").addEventListener("click",ev=>{
    if(S.tab!=="scenario")return;
    if(Date.now()-S.justDraggedAt<250)return;
    if(ev.target.closest(".card")||ev.target.closest(".zlab"))return;
    if(S.selectedNode!==null){flushPending();S.selectedNode=null;renderScenario();schedulePersist()}
  });
  document.addEventListener("keydown",ev=>{
    const tag=(ev.target&&ev.target.tagName||"").toLowerCase();
    const typing=tag==="input"||tag==="textarea"||tag==="select"||ev.target.isContentEditable;
    if(ev.key==="Escape"&&S.tab==="scenario"&&S.selectedNode!==null&&!typing){flushPending();S.selectedNode=null;renderScenario()}
    if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==="k"){ev.preventDefault();openPalette();return}
    if(typing)return;
    if(ev.key==="/") {ev.preventDefault();const box=$("searchBox");if(box){box.focus();box.select()}}
    if(ev.key.toLowerCase()==="o"&&S.selectedNode){ev.preventDefault();openEditorForNode(S.byId[S.selectedNode])}
    if(ev.key.toLowerCase()==="f"&&!ev.ctrlKey&&!ev.metaKey){ev.preventDefault();const a=activeView();fitView(a.canvas,a.view,()=>applyTransform(a.id,a.view))}
    if(ev.key==="+"||ev.key==="=") {ev.preventDefault();const a=activeView();a.view.k=Math.min(2.5,a.view.k*1.15);applyTransform(a.id,a.view)}
    if(ev.key==="-") {ev.preventDefault();const a=activeView();a.view.k=Math.max(.2,a.view.k*.87);applyTransform(a.id,a.view)}
    if(ev.key.toLowerCase()==="h") {ev.preventDefault();S.hideMeta=!S.hideMeta;if($("hideMeta"))$("hideMeta").checked=S.hideMeta;if(S.tab==="map")renderMap();else if(S.tab==="scenario")renderScenario();schedulePersist()}
    if(ev.key==="?") {ev.preventDefault();modal("Горячие клавиши","<div class=hotkeys><div><b>Esc</b> снять выделение</div><div><b>/</b> поиск</div><div><b>Ctrl+K</b> палитра узлов</div><div><b>O</b> открыть в редакторе</div><div><b>F</b> вписать</div><div><b>+ / −</b> зум</div><div><b>H</b> скрыть метаданные</div><div><b>Ctrl+Enter</b> сохранить поле</div></div>",[{label:"Сбросить раскладку",action:()=>{try{if(S.uiStorage)S.uiStorage.reset()}catch(_){ }location.reload()}},{label:"Закрыть",cls:"primary",action:()=>{}}])}
  });
  $("zIn").onclick=()=>{const a=activeView();a.view.k=Math.min(2.5,a.view.k*1.15);applyTransform(a.id,a.view);schedulePersist()};
  $("zOut").onclick=()=>{const a=activeView();a.view.k=Math.max(.2,a.view.k*.87);applyTransform(a.id,a.view);schedulePersist()};
  $("zReset").onclick=()=>{const a=activeView();a.view.k=1;a.view.x=30;a.view.y=30;applyTransform(a.id,a.view);schedulePersist()};
  $("zFit").onclick=()=>{const a=activeView();fitView(a.canvas,a.view,()=>applyTransform(a.id,a.view));schedulePersist()};
  $("hideMeta").onchange=()=>{S.hideMeta=$("hideMeta").checked;if(S.tab==="map")renderMap();else if(S.tab==="scenario")renderScenario();schedulePersist()};
  $("hideNested").onchange=()=>{S.hideNested=$("hideNested").checked;if(S.tab==="map")renderMap();else if(S.tab==="scenario")renderScenario();schedulePersist()};
  $("rescan").onclick=async()=>{
    await flushPending();
    try{
      applyGraph(await api("/api/rescan","POST",{}));
      if(S.tab==="map")renderMap();else if(S.tab==="scenario")renderScenario();else renderCreate();
      updateDiagBadge();toast("карта обновлена");schedulePersist();
    }catch(e){toast("ошибка скана: "+e.message)}
  };
  $("diagBtn").onclick=openDiagnostics;
  initTodoChip();
  if($("logoBtn"))$("logoBtn").onclick=showAbout;
  initSearch();
  updateDiagBadge();
  updateBriefBadge();
  refreshBriefStatus();
  setLive(false);
  startLive();
  window.addEventListener("beforeunload",ev=>{
    persistNow();
    if(S.formDirty&&S.selectedNode){
      ev.preventDefault();ev.returnValue="";
      try{
        const n=S.byId[S.selectedNode];
        const desc=$("fDesc")?$("fDesc").value:n.description;
        const acc=$("fAcc")?$("fAcc").value.split("\n").map(s=>s.trim()).filter(Boolean):n.acceptance;
        const dom=$("fDomain")?$("fDomain").value:(n.kind==="endpoint"?n.domain:"");
        fetch("/api/metadata/node",{method:"POST",keepalive:true,headers:{"Content-Type":"application/json","X-Studio-Token":studioToken()},body:JSON.stringify({node_id:n.id,description:desc,acceptance:acc,domain:dom})});
      }catch(_){}
    }
  });
  document.addEventListener("click",schedulePersist);
  document.addEventListener("change",schedulePersist);
  const initial=location.hash.replace(/^#/,"");
  setTab(["map","scenario","create"].includes(initial)?initial:S.tab);
}
boot();






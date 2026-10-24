import {api} from "./api.js";
import {openBriefDialog} from "./briefview.js";
import {
  $, S, applyGraph, applyTransform, areaSize, drawEdges, enableNodeDrag,
  enableZoneDrag, esc, proposedOverlap, schedulePersist, styleRect, toast,
  toastAction, zoneOverlaps, CREATE_KINDS, CREATE_LAYER_X, EDGE_NAMES, KIND_NAMES,
} from "./core.js";
import {clearStage, offsetInStage, sizeStage} from "./edges.js";
import {inferEdgeKind, proposedZoneRect} from "./geometry.js";

function createNodeById(id){return S.createState.nodes.find(n=>n.id===id)}
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
  drawEdges(svg,createBoxes(),S.createState.edges.map(e=>({from_node:e.from,to_node:e.to,kind:e.kind})));
}

function renderCreateEdgeList(){
  const box=$("createEdgeList");if(!box)return;
  box.innerHTML="";
  if(!S.createState.edges.length){
    box.innerHTML='<p class="hint">Связей нет. Потяните за точку справа от узла.</p>';
    return;
  }
  S.createState.edges.forEach((edge,index)=>{
    const from=createNodeById(edge.from),to=createNodeById(edge.to);
    const row=document.createElement("div");row.className="edge-item";
    row.innerHTML=`<span class="names" title="${esc((from?createNodeLabel(from):"?")+" → "+(to?createNodeLabel(to):"?"))}">${esc(from?createNodeLabel(from):"?")} → ${esc(to?createNodeLabel(to):"?")}</span>`+
      `<select>${Object.keys(EDGE_NAMES).map(k=>`<option value="${k}">${EDGE_NAMES[k]}</option>`).join("")}</select>`+
      `<button type="button" title="удалить связь">×</button>`;
    const sel=row.querySelector("select");sel.value=edge.kind;
    sel.onchange=()=>{edge.kind=sel.value;drawCreateEdges()};
    row.querySelector("button").onclick=()=>{S.createState.edges.splice(index,1);drawCreateEdges();renderCreateEdgeList()};
    box.appendChild(row);
  });
}
function renderCreateGraph(){
  const stage=$("createStage"),svg=$("createEdges");
  clearStage(stage,svg);
  if(!S.createState.nodes.length){
    const e=document.createElement("div");e.className="empty";e.style.top="8px";
    e.innerHTML="Добавьте узел кнопкой «+ узел» — он появится в области своего вида.";
    stage.appendChild(e);
  }
  const defs=[["endpoint","эндпоинт"],["scenario","сценарий"],["io","io"],["model","модель"],["test","тест"]];
  const zones={};
  defs.forEach(([kind,title])=>{
    const zone=document.createElement("div");
    zone.className="zone create-zone";zone.dataset.kind=kind;
    if(!S.createState.zones[kind])S.createState.zones[kind]={x:CREATE_LAYER_X[kind],y:40};
    const pos=S.createState.zones[kind];
    zone.style.left=pos.x+"px";zone.style.top=pos.y+"px";
    zone.style.width="240px";zone.style.height="70px";
    const lab=document.createElement("div");lab.className="zlab";
    lab.innerHTML=`<span class="grip" title="перетащить">⠿</span>${esc(title)} <span class="hint">· 0</span><button type="button" class="zone-add" data-kind="${kind}" title="Добавить узел">+</button>`;
    zone.appendChild(lab);stage.appendChild(zone);zones[kind]=zone;
    lab.querySelector(".zone-add").onclick=ev=>{ev.stopPropagation();addCreateNode(kind)};
    enableZoneDrag(zone,S.createView,
      (nx,ny)=>{
        const cx=Math.max(10,nx),cy=Math.max(10,ny);
        const rect={x:cx,y:cy,w:zone.offsetWidth,h:zone.offsetHeight};
        if(zoneOverlaps(zone,rect,createZones()))return;
        zone.style.left=cx+"px";zone.style.top=cy+"px";
        S.createState.zones[kind]={x:cx,y:cy};drawCreateEdges();
      },
      ()=>{
        S.createState.zones[kind]={x:parseFloat(zone.style.left)||0,y:parseFloat(zone.style.top)||0};
        recomputeCreateSizes();drawCreateEdges();
      });
  });
  S.createState.nodes.forEach(node=>{
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
  applyTransform("createView",S.createView);
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
  enableNodeDrag(e,"create",node.id,node.x,node.y,S.createView,
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
    id:"c"+(S.createState.seq++),kind,name:"",description:"",acceptance:"",domain:"",
    details:{http_method:"",path:"",scenario:""},
    x:14,y:nextCreateY(kind)
  };
  S.createState.nodes.push(node);
  renderCreateGraph();
}
function deleteCreateNode(id){
  S.createState.nodes=S.createState.nodes.filter(n=>n.id!==id);
  S.createState.edges=S.createState.edges.filter(e=>e.from!==id&&e.to!==id);
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
    const x2=(e.clientX-rect.left-S.createView.x)/(S.createView.k||1);
    const y2=(e.clientY-rect.top-S.createView.y)/(S.createView.k||1);
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
        if(S.createState.edges.some(edge=>edge.from===node.id&&edge.to===target.id)){
          toast("Такая связь уже есть");
        }else{
          S.createState.edges.push({from:node.id,to:target.id,kind:inferEdgeKind(node.kind,target.kind)});
          drawCreateEdges();renderCreateEdgeList();
        }
      }
    }
    S.createConnect=null;
  };
  S.createConnect={from:node.id};
  window.addEventListener("mousemove",move);
  window.addEventListener("mouseup",up);
}
function renderCreate(){
  renderCreateGraph();
}
function collectCreate(){
  if(!S.createState.nodes.length)throw new Error("Добавьте хотя бы один узел");
  const names=S.createState.nodes.map(n=>String(n.name||"").trim());
  if(names.some(n=>!n))throw new Error("Укажите имя (якорь) у каждого узла");
  if(new Set(names).size!==names.length)throw new Error("Имена узлов не должны повторяться");
  const nodes=S.createState.nodes.map((n,index)=>({
    kind:n.kind,
    name:names[index],
    description:n.description||"",
    acceptance:String(n.acceptance||"").split("\n").map(s=>s.trim()).filter(Boolean),
    domain:n.kind==="endpoint"?(n.domain||""):"",
    details:n.kind==="endpoint"
      ? {http_method:String(n.details.http_method||"").toLowerCase(),path:String(n.details.path||"").trim()}
      : n.kind==="io"
        ? {scenario:String(n.details.scenario||"").trim()}
        : {}
  }));
  const createById=S.createState.nodes.reduce((acc,n)=>{acc[n.id]=n;return acc},{});
  const edges=S.createState.edges.map(e=>({
    from:String((createById[e.from]&&createById[e.from].name)||"").trim(),
    to:String((createById[e.to]&&createById[e.to].name)||"").trim(),
    kind:e.kind
  })).filter(e=>e.from&&e.to);
  return {nodes,edges};
}
async function submitCreate(){
  const result=$("createResult");result.style.display="none";
  try{
    const data=await api("/api/create","POST",collectCreate());
    applyGraph(data.graph);
    S.createState.nodes=[];S.createState.edges=[];S.createState.seq=1;
    renderCreateGraph();
    result.style.display="block";
    const lines=["Созданы файлы-скелеты с TODO:"];
    (data.created||[]).forEach(c=>lines.push(`  ${c.path}  — ${KIND_NAMES[c.kind]||c.kind} ${c.name}`));
    lines.push("");lines.push("Описания и связи записаны в .studio/metadata.json. Код проекта не изменялся: в новых файлах только TODO.");
    result.textContent=lines.join("\n");
    toastAction("файлы созданы","Сформировать ТЗ",()=>openBriefDialog());
  }catch(e){
    result.style.display="block";result.textContent="Ошибка: "+e.message;
  }
}

export {renderCreate, submitCreate, renderCreateGraph};

import {$,S,applyTransform,drawEdges,esc,KIND_NAMES,EDGE_COLORS,EDGE_NAMES,nodeMeta,methodOfEndpoint,urlOfEndpoint,openEditorForNode,toast,schedulePersist,enableNodeDrag} from "./core.js";
import {clearStage,measure,sizeStage} from "./edges.js";
import {matchesAll} from "./search.js";
import {renderInspector} from "./inspectorview.js";

const W=210,H=126,GAP_X=150,GAP_Y=36;
const parentOf=edge=>edge.kind==="tests"?edge.to_node:edge.from_node;
const childOf=edge=>edge.kind==="tests"?edge.from_node:edge.to_node;
function model(){
  const nodes=(S.G.nodes||[]).map(n=>({...n,viewId:n.id,synthetic:false}));
  const byId=Object.fromEntries(nodes.map(n=>[n.id,n]));
  const edges=(S.G.edges||[]).map(e=>({from_node:parentOf(e),to_node:childOf(e),kind:e.kind,label:e.label}));
  const effectiveDomains=n=>String(n.domain||"").split(",").map(value=>value.trim()).filter(Boolean).map(domain=>S.mapMode==="plan"&&S.G.domain_changes?.[domain]?.name||domain);
  const methods=new Map();
  for(const endpoint of nodes.filter(n=>n.kind==="endpoint")){
    const method=methodOfEndpoint(endpoint)||"Метод не указан";if(!methods.has(method))methods.set(method,[]);methods.get(method).push(endpoint);
  }
  for(const [method,endpoints] of methods){
    const mid=`@method:${method}`;nodes.push({id:mid,viewId:mid,kind:"method",label:method,synthetic:true,planned:false,details:{count:endpoints.length}});
    const domains=new Map();for(const endpoint of endpoints){for(const d of (effectiveDomains(endpoint).length?effectiveDomains(endpoint):["Без домена"])){if(!domains.has(d))domains.set(d,[]);domains.get(d).push(endpoint)}}
    for(const [domain,items] of domains){
      const rename=Object.entries(S.G.domain_changes||{}).find(([old,v])=>v?.name===domain);
      const plannedDomain=(S.G.planned_domains||[]).find(item=>item.name===domain);
      const plannedGroup=!!rename||!!plannedDomain||items.every(n=>n.planned)&&items.length>0;
      const did=plannedDomain?`@domain-plan:${plannedDomain.id}`:`@domain:${domain}`;
      if(!byId[did]&&!nodes.some(n=>n.id===did))nodes.push({id:did,viewId:did,kind:"domain",label:domain,synthetic:true,planned:plannedGroup,sourceDomain:rename?.[0]||(S.G.domain_changes?.[domain]?domain:""),domainPlanId:plannedDomain?.id||"",domainRecord:plannedDomain||null,details:{count:items.length}});
      edges.push({from_node:mid,to_node:did,kind:"uses",label:"домен",planned:plannedGroup});
      for(const item of items)edges.push({from_node:did,to_node:item.id,kind:"uses",label:"эндпоинт",planned:!!item.planned});
    }
    if(S.mapMode==="fact")for(const [source,change] of Object.entries(S.G.domain_changes||{})){
      if(!domains.has(source))continue;
      const id=`@domain-change:${source}`;
      nodes.push({id,viewId:id,kind:"domain",label:change.name||source,synthetic:true,planned:true,sourceDomain:source,details:{count:0}});
      edges.push({from_node:mid,to_node:id,kind:"uses",label:"план переименования",planned:true});
    }
  }
  for(const d of S.G.planned_domains||[]){
    if(!nodes.some(n=>n.id===`@domain-plan:${d.id}`))nodes.push({id:`@domain-plan:${d.id}`,viewId:`@domain-plan:${d.id}`,kind:"domain",label:d.name,synthetic:true,planned:true,domainPlanId:d.id,domainRecord:d,details:{count:0}});
  }
  for(const n of nodes.filter(item=>!item.synthetic&&item.planned_change&&(item.planned_change.note||item.planned_change.acceptance?.length))){
    const id=`@change:${n.id}`;
    nodes.push({id,viewId:id,kind:"change",label:"Плановая правка",synthetic:true,planned:true,sourceNodeId:n.id,plannedChange:n.planned_change,details:{name:n.label}});
    edges.push({from_node:n.id,to_node:id,kind:"uses",label:"плановая правка",planned:true});
  }
  return {nodes,edges};
}
function visibleClass(node){
  if(S.mapMode==="fact"&&(node.planned||node.planned_change&&Object.keys(node.planned_change).length))return " faded";
  if(S.mapMode==="plan"&&!node.planned&&!node.synthetic)return " faded";
  return "";
}
function positionGraph(nodes,edges){
  const byId=Object.fromEntries(nodes.map(n=>[n.id,n])),children=new Map(),parents=new Map();
  nodes.forEach(n=>{children.set(n.id,[]);parents.set(n.id,[])});
  edges.forEach(e=>{if(!byId[e.from_node]||!byId[e.to_node]||e.from_node===e.to_node)return;children.get(e.from_node).push(e.to_node);parents.get(e.to_node).push(e.from_node)});
  const rank=new Map(),queue=nodes.filter(n=>!parents.get(n.id).length).map(n=>n.id),visited=new Set();queue.forEach(id=>rank.set(id,0));
  for(let i=0;i<queue.length;i++){const id=queue[i];if(visited.has(id))continue;visited.add(id);for(const child of children.get(id)){if(rank.has(child))continue;rank.set(child,(rank.get(id)||0)+1);queue.push(child)}}
  nodes.forEach(n=>{if(!rank.has(n.id))rank.set(n.id,0)});
  const levels=new Map();for(const n of nodes){const r=rank.get(n.id);if(!levels.has(r))levels.set(r,[]);levels.get(r).push(n)}
  const positions=new Map(),widths=new Map(),placed=[];let x=44;
  for(const r of [...levels.keys()].sort((a,b)=>a-b)){
    const items=levels.get(r).sort((a,b)=>String(a.label).localeCompare(String(b.label),"ru"));let y=42;
    items.forEach(n=>{
      const saved=S.mapPos.get(n.id),w=Math.max(154,250-r*12),p=saved?{...saved}:{x,y};widths.set(n.id,w);
      while(placed.some(q=>p.x<q.x+q.w+12&&p.x+w+12>q.x&&p.y<q.y+H+GAP_Y&&p.y+H+GAP_Y>q.y))p.y+=H+GAP_Y;
      positions.set(n.id,p);placed.push({x:p.x,y:p.y,w});y=Math.max(y,p.y+H+GAP_Y);
    });x+=250+GAP_X;
  }
  return {positions,widths,children,parents};
}
function draw(){
  const stage=$("mapStage"),svg=$("mapEdges");if(!stage||!svg)return;
  clearStage(stage,svg);const {nodes,edges}=model(),{positions,widths,children}=positionGraph(nodes,edges),byId=Object.fromEntries(nodes.map(n=>[n.id,n]));
  if(S.selectedNode&&!byId[S.selectedNode]){
    const previous=S.mapViewNodes?.[S.selectedNode],source=previous?.sourceDomain;
    if(source){const match=nodes.find(n=>n.sourceDomain===source&&(S.mapMode==="plan"?n.planned:!n.planned));if(match)S.selectedNode=match.id}
    if(String(S.selectedNode).startsWith("@change:"))S.selectedNode=null;
  }
  S.mapViewNodes=byId;
  const filtered=nodes.filter(n=>!S.lastSearchQuery||matchesAll(S.lastSearchQuery,[n.label,n.domain,n.description,n.file_path,n.kind,nodeMeta(n),n.planned_change?.note,n.plannedChange?.note,...(n.planned_change?.acceptance||[]),...(n.plannedChange?.acceptance||[])]));
  const shown=new Set(filtered.map(n=>n.id));
  // Keep connected context when searching: matching nodes and their immediate ancestors.
  for(const e of edges)if(shown.has(e.to_node)&&byId[e.from_node])shown.add(e.from_node);
  if(!shown.size){const empty=document.createElement("div");empty.className="empty";empty.textContent="Совпадений нет. Очистите поиск.";stage.appendChild(empty)}
  nodes.filter(n=>shown.has(n.id)).forEach(n=>{
    const p=positions.get(n.id)||{x:44,y:42},card=document.createElement("article");card.className=`card map-node k-${n.kind}${n.planned?" planned node--todo":""}${visibleClass(n)}${S.selectedNode===n.id?" sel":""}${n.synthetic?" synthetic":""}`;card.dataset.id=n.id;card.style.left=`${p.x}px`;card.style.top=`${p.y}px`;card.style.width=`${widths.get(n.id)||W}px`;
    const kind=n.kind==="method"?"HTTP-метод":n.kind==="domain"?"домен":n.kind==="change"?"план правки":KIND_NAMES[n.kind]||n.kind;
    const detail=n.kind==="endpoint"?`${methodOfEndpoint(n)} ${urlOfEndpoint(n)}\n${(n.details||{}).function_name||""}`:n.kind==="change"?S.byId[n.sourceNodeId]?.label||"фактический узел":n.synthetic?`${n.details?.count||0} узлов`:`${nodeMeta(n)||n.file_path+(n.line?":"+n.line:"")}`;
    const note=n.plannedChange?.note;
    card.innerHTML=`<div class="hd"><span class="grip" title="Перетащить узел и потомков">⠿</span><span class="chip">${esc(kind)}</span>${n.planned?'<span class="chip todo">план</span>':""}<span class="t">${esc(n.label)}</span>${!n.synthetic&&n.file_path?'<button class="open-editor" type="button" title="Открыть в редакторе">↗</button>':""}</div><div class="mt">${esc(detail)}</div>${n.kind==="change"&&note&&!S.hideMeta?`<div class="planned-note">${esc(note)}</div>`:""}${n.kind!=="change"?'<button type="button" class="map-add-child" aria-label="Добавить потомка" title="Добавить потомка">＋</button>':""}`;
    card.onclick=ev=>{if(ev.target.closest("button,.grip"))return;ev.stopPropagation();if(Date.now()-S.justDraggedAt<250)return;S.selectedNode=n.id;renderInspector();draw();schedulePersist()};
    card.tabIndex=0;card.setAttribute("role","button");card.setAttribute("aria-label",`${kind}: ${n.label}`);card.addEventListener("keydown",ev=>{if(ev.target!==card)return;if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();S.selectedNode=n.id;renderInspector();draw();schedulePersist()}});
    card.querySelector(".map-add-child")?.addEventListener("click",ev=>{
      ev.stopPropagation();
      if(n.kind==="method")S.selectedNode="__new_domain__";
      else S.selectedNode=n.id;
      renderInspector();
      if(n.kind==="domain")$("domainAddEndpoint")?.click();
      draw();
      document.querySelector("#graphNodeCreate input[name=name]")?.focus();
    });
    card.querySelector(".open-editor")?.addEventListener("click",ev=>{ev.stopPropagation();openEditorForNode(S.byId[n.id])});
    stage.appendChild(card);
    enableNodeDrag(card,"map",n.id,p.x,p.y,S.mapView,(nx,ny)=>{
      const dx=nx-p.x,dy=ny-p.y,moving=new Set([n.id]),todo=[n.id];
      while(todo.length){for(const c of children.get(todo.pop())||[])if(!moving.has(c)){moving.add(c);todo.push(c)}}
      const candidates=[...moving].map(id=>({id,x:(positions.get(id)?.x||0)+(id===n.id?dx:dx),y:(positions.get(id)?.y||0)+dy}));
      if(candidates.some(a=>nodes.some(other=>{const q=positions.get(other.id)||{x:0,y:0};return !moving.has(other.id)&&shown.has(other.id)&&a.x+widths.get(a.id)+12>q.x&&a.x<q.x+widths.get(other.id)+12&&a.y<q.y+H+12&&a.y+H+12>q.y})))return;
      candidates.forEach(a=>{const el=stage.querySelector(`[data-id="${CSS.escape(a.id)}"]`);if(el){el.style.left=`${a.x}px`;el.style.top=`${a.y}px`}S.mapPos.set(a.id,{x:a.x,y:a.y})});S.justDraggedAt=Date.now();
      const liveBoxes=measure(stage);S.mapBoxes=liveBoxes;drawEdges(svg,liveBoxes,edges.filter(e=>shown.has(e.from_node)&&shown.has(e.to_node)));sizeStage(stage,svg);
    },()=>{schedulePersist()});
  });
  const boxes=measure(stage);S.mapBoxes=boxes;
  const shownEdges=edges.filter(e=>shown.has(e.from_node)&&shown.has(e.to_node)).map(e=>({...e,todo:!!(byId[e.from_node]?.planned||byId[e.to_node]?.planned||e.planned)}));
  drawEdges(svg,boxes,shownEdges);sizeStage(stage,svg);applyTransform("mapView",S.mapView);
  const mode=$("mapModeCaption");if(mode)mode.textContent=S.mapMode==="fact"?"Фактическая структура проекта":"Планируемая структура и изменения";
}
function renderMap(){draw()}
export {renderMap};

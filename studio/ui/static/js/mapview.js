import {$,S,applyTransform,drawEdges,esc,KIND_NAMES,EDGE_COLORS,EDGE_NAMES,nodeMeta,methodOfEndpoint,urlOfEndpoint,openEditorForNode,toast,schedulePersist,enableNodeDrag} from "./core.js";
import {clearStage,measure,sizeStage} from "./edges.js";
import {clampGroupDelta} from "./geometry.js";
import {matchesAll} from "./search.js";
import {renderInspector} from "./inspectorview.js";

const W=210,H=126,GAP_Y=48;
const parentOf=edge=>edge.kind==="tests"?edge.to_node:edge.from_node;
const childOf=edge=>edge.kind==="tests"?edge.from_node:edge.to_node;
function model(){
  const nodes=(S.G.nodes||[]).map(n=>({...n,viewId:n.id,synthetic:false}));
  const canonicalById=Object.fromEntries(nodes.map(n=>[n.id,n]));
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
      if(!canonicalById[did]&&!nodes.some(n=>n.id===did))nodes.push({id:did,viewId:did,kind:"domain",label:domain,synthetic:true,planned:plannedGroup,sourceDomain:rename?.[0]||(S.G.domain_changes?.[domain]?domain:""),domainPlanId:plannedDomain?.id||"",domainRecord:plannedDomain||null,details:{count:items.length}});
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
  // Project descendants once per endpoint. Copies keep the canonical id so
  // editing and selection still operate on the same underlying graph node.
  const endpointNodes=nodes.filter(n=>n.kind==="endpoint");
  const outgoing=new Map(nodes.map(n=>[n.id,[]]));
  edges.forEach(e=>{if(outgoing.has(e.from_node))outgoing.get(e.from_node).push(e)});
  const projectedNodes=[],projectedEdges=[],projectedIds=new Set(),selectedViews={};
  const inboundGroups=new Set(nodes.filter(n=>n.kind==="method"||n.kind==="domain").map(n=>n.id));
  for(const endpoint of endpointNodes){
    const reachable=new Set([endpoint.id]),queue=[endpoint.id];
    for(let i=0;i<queue.length;i++)for(const edge of outgoing.get(queue[i])||[]){
      const child=canonicalById[edge.to_node]||nodes.find(n=>n.id===edge.to_node);
      if(!child||child.kind==="endpoint"||child.kind==="method"||child.kind==="domain"||reachable.has(child.id))continue;
      reachable.add(child.id);queue.push(child.id);
    }
    const viewByCanonical=new Map();
    for(const id of reachable){
      const source=nodes.find(n=>n.id===id);if(!source)continue;
      const viewId=`@branch:${encodeURIComponent(endpoint.id)}:${encodeURIComponent(id)}`;
      const copy={...source,canonicalId:id,viewId,branchId:endpoint.id};
      projectedNodes.push(copy);projectedIds.add(id);viewByCanonical.set(id,viewId);
      if(!selectedViews[id])selectedViews[id]=copy;
    }
    for(const edge of edges){
      if(viewByCanonical.has(edge.from_node)&&viewByCanonical.has(edge.to_node))
        projectedEdges.push({...edge,from_node:viewByCanonical.get(edge.from_node),to_node:viewByCanonical.get(edge.to_node),canonicalFrom:edge.from_node,canonicalTo:edge.to_node});
      else if(inboundGroups.has(edge.from_node)&&edge.to_node===endpoint.id)
        projectedEdges.push({...edge,from_node:edge.from_node,to_node:viewByCanonical.get(endpoint.id),canonicalFrom:edge.from_node,canonicalTo:endpoint.id});
    }
  }
  const remaining=nodes.filter(n=>!projectedIds.has(n.id));
  const viewNodes=[...remaining,...projectedNodes];
  const viewEdges=edges.filter(e=>!projectedIds.has(e.from_node)&&!projectedIds.has(e.to_node));
  viewEdges.push(...projectedEdges);
  for(const n of remaining)if(!selectedViews[n.id])selectedViews[n.id]=n;
  return {nodes:viewNodes,edges:viewEdges,selectedViews};
}
function visibleClass(node){
  if(S.mapMode==="fact"&&(node.planned||node.planned_change&&Object.keys(node.planned_change).length))return " faded";
  if(S.mapMode==="plan"&&!node.planned&&!node.synthetic)return " faded";
  return "";
}
function positionGraph(nodes,edges){
  const byView=Object.fromEntries(nodes.map(n=>[n.viewId,n])),children=new Map(),parents=new Map();
  nodes.forEach(n=>{children.set(n.viewId,[]);parents.set(n.viewId,[])});
  edges.forEach(e=>{if(!byView[e.from_node]||!byView[e.to_node]||e.from_node===e.to_node)return;children.get(e.from_node).push(e.to_node);parents.get(e.to_node).push(e.from_node)});
  const positions=new Map(),widths=new Map();
  const layoutTree=(root,nodeIds,treeEdges,x,y)=>{
    const allowed=new Set(nodeIds),treeChildren=new Map(nodeIds.map(id=>[id,[]])),seen=new Set([root]),queue=[root],depth=new Map([[root,0]]);
    for(let i=0;i<queue.length;i++){
      const id=queue[i],next=[...new Set((treeEdges.get(id)||[]).filter(child=>allowed.has(child)))].sort((a,b)=>String(byView[a]?.label||a).localeCompare(String(byView[b]?.label||b),"ru"));
      for(const child of next)if(!seen.has(child)){seen.add(child);depth.set(child,(depth.get(id)||0)+1);treeChildren.get(id).push(child);queue.push(child)}
    }
    const subtreeWidth=new Map(),subtreeHeight=new Map(),measuring=new Set();
    const measureTree=id=>{
      if(subtreeWidth.has(id)||measuring.has(id))return;
      measuring.add(id);for(const child of treeChildren.get(id)||[])measureTree(child);measuring.delete(id);
      const d=depth.get(id)||0,nodeWidth=Math.max(154,250-d*14),kids=treeChildren.get(id)||[];
      widths.set(id,nodeWidth);subtreeWidth.set(id,Math.max(nodeWidth,kids.reduce((sum,child)=>sum+subtreeWidth.get(child),0)+Math.max(0,kids.length-1)*44));
      subtreeHeight.set(id,H+(kids.length?GAP_Y+Math.max(...kids.map(child=>subtreeHeight.get(child))):0));
    };
    measureTree(root);
    const place=(id,left,top)=>{
      const nodeWidth=widths.get(id),span=subtreeWidth.get(id);positions.set(id,{x:left+(span-nodeWidth)/2,y:top});
      const kids=treeChildren.get(id)||[];let kidLeft=left,childTop=top+H+GAP_Y;
      for(const child of kids){place(child,kidLeft,childTop);kidLeft+=subtreeWidth.get(child)+44}
    };
    place(root,x,y);
    return {width:subtreeWidth.get(root)||250,height:subtreeHeight.get(root)||H,treeChildren};
  };
  const endpointBranches=new Map();
  for(const n of nodes)if(n.branchId){if(!endpointBranches.has(n.branchId))endpointBranches.set(n.branchId,[]);endpointBranches.get(n.branchId).push(n)}
  const clusters=[];
  for(const [branchId,branchNodes] of endpointBranches){
    const root=branchNodes.find(n=>n.kind==="endpoint");if(!root)continue;
    const ids=branchNodes.map(n=>n.viewId),idSet=new Set(ids),treeEdges=new Map(ids.map(id=>[id,[]]));
    edges.filter(e=>idSet.has(e.from_node)&&idSet.has(e.to_node)).forEach(e=>treeEdges.get(e.from_node).push(e.to_node));
    clusters.push({root:root.viewId,ids,treeEdges,label:root.label,domain:String(root.domain||"")});
  }
  clusters.sort((a,b)=>a.domain.localeCompare(b.domain,"ru")||a.label.localeCompare(b.label,"ru"));
  let branchY=360,rowX=44,rowWidth=0,rowHeight=0;const maxRowWidth=1700;
  for(const cluster of clusters){
    const measured=layoutTree(cluster.root,cluster.ids,cluster.treeEdges,0,0);
    if(rowX>44&&rowWidth+measured.width>maxRowWidth){branchY+=rowHeight+110;rowX=44;rowWidth=0;rowHeight=0}
    layoutTree(cluster.root,cluster.ids,cluster.treeEdges,rowX,branchY);
    rowX+=measured.width+110;rowWidth+=measured.width+110;rowHeight=Math.max(rowHeight,measured.height);
  }
  const methods=nodes.filter(n=>n.kind==="method").sort((a,b)=>a.label.localeCompare(b.label,"ru"));
  methods.forEach((n,i)=>{widths.set(n.viewId,240);positions.set(n.viewId,{x:44+i*310,y:42})});
  const domains=nodes.filter(n=>n.kind==="domain").sort((a,b)=>a.label.localeCompare(b.label,"ru"));
  domains.forEach((n,i)=>{widths.set(n.viewId,230);positions.set(n.viewId,{x:44+i*300,y:190})});
  let orphanY=clusters.length?branchY+rowHeight+110:340,orphanX=44,orphanRowHeight=0;
  const orphanNodes=nodes.filter(n=>!n.branchId&&n.kind!=="method"&&n.kind!=="domain"),orphanIds=new Set(orphanNodes.map(n=>n.viewId)),orphanEdges=new Map(orphanNodes.map(n=>[n.viewId,[]]));
  edges.forEach(e=>{if(orphanIds.has(e.from_node)&&orphanIds.has(e.to_node))orphanEdges.get(e.from_node).push(e.to_node)});
  const orphanRoots=orphanNodes.filter(n=>!(parents.get(n.viewId)||[]).some(id=>orphanIds.has(id)));
  const placedOrphans=new Set();
  for(const root of [...orphanRoots,...orphanNodes]){
    if(placedOrphans.has(root.viewId))continue;
    const measured=layoutTree(root.viewId,[...orphanIds],orphanEdges,0,0),treeIds=new Set();
    const stack=[root.viewId];while(stack.length){const id=stack.pop();if(treeIds.has(id))continue;treeIds.add(id);placedOrphans.add(id);for(const child of orphanEdges.get(id)||[])stack.push(child)}
    const clusterWidth=Math.max(measured.width,230);
    if(orphanX>44&&orphanX+clusterWidth>maxRowWidth){orphanY+=orphanRowHeight+100;orphanX=44;orphanRowHeight=0}
    layoutTree(root.viewId,[...treeIds],orphanEdges,orphanX,orphanY);orphanX+=clusterWidth+90;orphanRowHeight=Math.max(orphanRowHeight,measured.height);
  }
  for(const n of nodes){
    const saved=S.mapPos.get(n.viewId)||(!n.branchId?S.mapPos.get(n.id):null);
    if(saved)positions.set(n.viewId,{...saved});
  }
  const placed=[];
  for(const n of [...nodes].sort((a,b)=>positions.get(a.viewId).y-positions.get(b.viewId).y||positions.get(a.viewId).x-positions.get(b.viewId).x)){
    const p=positions.get(n.viewId),w=widths.get(n.viewId)||W;
    while(placed.some(q=>p.x<q.x+q.w+12&&p.x+w+12>q.x&&p.y<q.y+H+12&&p.y+H+12>q.y))p.y+=H+12;
    placed.push({x:p.x,y:p.y,w});
  }
  return {positions,widths,children,parents};
}
function draw(){
  const stage=$("mapStage"),svg=$("mapEdges");if(!stage||!svg)return;
  clearStage(stage,svg);const graph=model(),{nodes,edges,selectedViews}=graph,{positions,widths,children}=positionGraph(nodes,edges),byView=Object.fromEntries(nodes.map(n=>[n.viewId,n]));
  if(S.selectedNode&&!selectedViews[S.selectedNode]){
    const previous=S.mapViewNodes?.[S.selectedNode],source=previous?.sourceDomain;
    if(source){const match=Object.values(selectedViews).find(n=>n.sourceDomain===source&&(S.mapMode==="plan"?n.planned:!n.planned));if(match)S.selectedNode=match.id}
    if(String(S.selectedNode).startsWith("@change:"))S.selectedNode=null;
  }
  S.mapViewNodes=selectedViews;
  const filtered=nodes.filter(n=>!S.lastSearchQuery||matchesAll(S.lastSearchQuery,[n.label,n.domain,n.description,n.file_path,n.kind,nodeMeta(n),n.planned_change?.note,n.plannedChange?.note,...(n.planned_change?.acceptance||[]),...(n.plannedChange?.acceptance||[])]));
  const shown=new Set(filtered.map(n=>n.viewId));
  // Keep every ancestor so a search result stays inside its endpoint branch.
  let grew=true;while(grew){grew=false;for(const e of edges)if(shown.has(e.to_node)&&byView[e.from_node]&&!shown.has(e.from_node)){shown.add(e.from_node);grew=true}}
  if(!shown.size){const empty=document.createElement("div");empty.className="empty";empty.textContent="Совпадений нет. Очистите поиск.";stage.appendChild(empty)}
  nodes.filter(n=>shown.has(n.viewId)).forEach(n=>{
    const p=positions.get(n.viewId)||{x:44,y:42},card=document.createElement("article");card.className=`card map-node k-${n.kind}${n.planned?" planned node--todo":""}${visibleClass(n)}${S.selectedNode===n.id?" sel":""}${n.synthetic?" synthetic":""}`;card.dataset.id=n.viewId;card.dataset.nodeId=n.id;card.style.left=`${p.x}px`;card.style.top=`${p.y}px`;card.style.width=`${widths.get(n.viewId)||W}px`;
    const kind=n.kind==="method"?"HTTP-метод":n.kind==="domain"?"домен":n.kind==="change"?"план правки":KIND_NAMES[n.kind]||n.kind;
    const detail=n.kind==="endpoint"?`${methodOfEndpoint(n)} ${urlOfEndpoint(n)}\n${(n.details||{}).function_name||""}`:n.kind==="change"?S.byId[n.sourceNodeId]?.label||"фактический узел":n.synthetic?`${n.details?.count||0} узлов`:`${nodeMeta(n)||n.file_path+(n.line?":"+n.line:"")}`;
    const note=n.plannedChange?.note;
    card.innerHTML=`<div class="hd"><span class="grip" title="Перетащить узел и потомков этой ветки">⠿</span><span class="chip">${esc(kind)}</span>${n.planned?'<span class="chip todo">план</span>':""}<span class="t">${esc(n.label)}</span>${!n.synthetic&&n.file_path?'<button class="open-editor" type="button" title="Открыть в редакторе">↗</button>':""}</div><div class="mt">${esc(detail)}</div>${n.kind==="change"&&note&&!S.hideMeta?`<div class="planned-note">${esc(note)}</div>`:""}${n.kind!=="change"?'<button type="button" class="map-add-child" aria-label="Добавить потомка" title="Добавить потомка">＋</button>':""}`;
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
    let currentDx=0,currentDy=0;
    enableNodeDrag(card,"map",n.viewId,p.x,p.y,S.mapView,(nx,ny)=>{
      const requestedDx=nx-p.x,requestedDy=ny-p.y,moving=new Set([n.viewId]),todo=[n.viewId];
      while(todo.length){for(const c of children.get(todo.pop())||[])if(!moving.has(c)){moving.add(c);todo.push(c)}}
      const movingRects=[...moving].map(id=>{const p0=positions.get(id);return{x:p0.x,y:p0.y,w:widths.get(id)||W,h:H}});
      const stationaryRects=nodes.filter(other=>!moving.has(other.viewId)).map(other=>{const p0=positions.get(other.viewId);return{x:p0.x,y:p0.y,w:widths.get(other.viewId)||W,h:H}});
      const delta=clampGroupDelta(movingRects,stationaryRects,{x:currentDx,y:currentDy},{x:requestedDx,y:requestedDy});
      currentDx=delta.x;currentDy=delta.y;
      const candidates=[...moving].map(id=>({id,x:(positions.get(id)?.x||0)+dx,y:(positions.get(id)?.y||0)+dy}));
      candidates.forEach(a=>{const el=stage.querySelector(`[data-id="${CSS.escape(a.id)}"]`);if(el){el.style.left=`${a.x}px`;el.style.top=`${a.y}px`}S.mapPos.set(a.id,{x:a.x,y:a.y})});S.justDraggedAt=Date.now();
      const liveBoxes=measure(stage);S.mapBoxes=liveBoxes;drawEdges(svg,liveBoxes,edges.filter(e=>shown.has(e.from_node)&&shown.has(e.to_node)));sizeStage(stage,svg);
    },()=>{schedulePersist();draw()});
  });
  const boxes=measure(stage);S.mapBoxes=boxes;
  const shownEdges=edges.filter(e=>shown.has(e.from_node)&&shown.has(e.to_node)).map(e=>({...e,todo:!!(byView[e.from_node]?.planned||byView[e.to_node]?.planned||e.planned)}));
  drawEdges(svg,boxes,shownEdges);sizeStage(stage,svg);applyTransform("mapView",S.mapView);
  const mode=$("mapModeCaption");if(mode)mode.textContent=S.mapMode==="fact"?"Фактическая структура проекта":"Планируемая структура и изменения";
}
function renderMap(){draw()}
export {renderMap};

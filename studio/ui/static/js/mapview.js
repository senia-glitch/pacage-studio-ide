import {api} from "./api.js";
import {$,S,applyTransform,drawEdges,esc,KIND_NAMES,EDGE_COLORS,EDGE_NAMES,nodeMeta,methodOfEndpoint,urlOfEndpoint,openEditorForNode,toast,schedulePersist,enableNodeDrag,applyGraph,hooks,modal} from "./core.js";
import {clearStage,measure,sizeStage} from "./edges.js";
import {clampGroupDelta} from "./geometry.js";
import {matchesAll} from "./search.js";
import {renderInspector} from "./inspectorview.js";
import {CHILD_KINDS,relationFor} from "./relations.js";

const W=210,H=126,GAP_Y=48,NEW_H=350,NEW_W=280,MIN_H=126,MAX_H=460,META_H=58;
/* Высота карточки: заданная пользователем (ресайз за нижнюю кромку) > авто-рост
   под видимые метаданные > базовая. Всё в границах [MIN_H, MAX_H]. */
function nodeAutoHeight(n){
  if(!n)return H;
  if(n.pending)return NEW_H;
  const custom=S.mapH.get(n.id);
  if(Number.isFinite(custom))return Math.max(MIN_H,Math.min(MAX_H,custom));
  if(!S.hideMeta){
    const hasMeta=String(n.description||"").trim()
      ||(Array.isArray(n.acceptance)&&n.acceptance.some(x=>String(x).trim()))
      ||String(n.domain||"").trim();
    if(hasMeta)return Math.min(MAX_H,H+META_H);
  }
  return H;
}
/* Неопределённый узел: один на канвасе, создаётся кнопкой ＋ и определяет
   будущего потомка (тип/название/задача/критерии) прямо на карте. */
let pendingChild=null;
let staleLayoutPurged=false;
function closePending(){pendingChild=null;for(const key of [...S.mapPos.keys()])if(String(key).startsWith("@new:"))S.mapPos.delete(key)}

/* Перетащили эндпоинт на метод/домен — назначить эндпоинту этот домен.
   Плановые узлы обновляются в плане, фактические — в metadata.json (код не меняется). */
async function assignEndpointDomain(node,newDomain){
  const endpointId=node.canonicalId||node.id;
  try{
    let d;
    if(node.planned&&String(node.id).startsWith("studio:")){
      d=await api("/api/metadata/graph-node","POST",{
        node_id:endpointId,kind:"endpoint",
        name:(node.details||{}).function_name||node.label,
        description:node.description||"",
        acceptance:node.acceptance||[],
        domain:newDomain,
        http_method:(node.details||{}).http_method||"",
        path:(node.details||{}).path||"",
      });
    }else{
      d=await api("/api/metadata/node","POST",{node_id:endpointId,domain:newDomain});
    }
    applyGraph(d.graph);
    // Позиции копий ветки устарели — ветка аккуратно раскладывается под новым доменом.
    const prefix="@branch:"+encodeURIComponent(endpointId)+":";
    for(const key of [...S.mapPos.keys()])if(String(key).startsWith(prefix))S.mapPos.delete(key);
    hooks.renderMap();
    toast(newDomain?`Эндпоинт перенесён в домен «${newDomain}»`:"Домен убран — эндпоинт в «Без домена»");
  }catch(error){toast("Не удалось назначить домен: "+error.message)}
}
async function attachScenarioToMethod(node,methodNode,choices){
  if(!choices.length){toast("В этой группе нет эндпоинтов — прикреплять не к чему");return}
  const attach=async endpointId=>{
    try{
      const d=await api("/api/metadata/graph-node","POST",{
        node_id:node.id,kind:"scenario",name:node.label,
        description:node.description||"",acceptance:node.acceptance||[],domain:node.domain||"",
        parent_id:endpointId,edge_kind:"dispatch",
      });
      applyGraph(d.graph);
      S.mapPos.delete(node.id);
      hooks.renderMap();
      toast(`Сценарий «${node.label}» прикреплён к ветке эндпоинта`);
    }catch(error){window.__attachErr=String(error&&error.message||error);toast("Не удалось прикрепить сценарий: "+error.message)}
  };
  if(choices.length===1){await attach(choices[0].id);return}
  const options=choices.map(choice=>({label:choice.label,action:()=>attach(choice.id)}));
  options.push({label:"Отмена",action:()=>{}});
  modal("К какому эндпоинту прикрепить сценарий?",`<p class="hint">У метода ${esc(methodNode.label)} несколько эндпоинтов — выберите ветку.</p>`,options);
}
function dropDomainOf(target){
  if(!target)return null;
  const name=target.kind==="method"?target.domain:target.label;
  if(name==="Без домена")return"";
  return name||null;
}
const parentOf=edge=>edge.kind==="tests"?edge.to_node:edge.from_node;
const childOf=edge=>edge.kind==="tests"?edge.from_node:edge.to_node;
function model(){
  const nodes=(S.G.nodes||[]).map(n=>({...n,viewId:n.id,synthetic:false}));
  const canonicalById=Object.fromEntries(nodes.map(n=>[n.id,n]));
  const edges=(S.G.edges||[]).map(e=>({from_node:parentOf(e),to_node:childOf(e),kind:e.kind,label:e.label}));
  const effectiveDomains=n=>String(n.domain||"").split(",").map(value=>value.trim()).filter(Boolean).map(domain=>S.mapMode==="plan"&&S.G.domain_changes?.[domain]?.name||domain);
  // Карта доменов: домен — стартовая точка, под ним его HTTP-методы,
  // под методами — эндпоинты этого метода и домена.
  const domainEntries=new Map();
  for(const endpoint of nodes.filter(n=>n.kind==="endpoint")){
    const method=methodOfEndpoint(endpoint)||"Метод не указан";
    for(const d of (effectiveDomains(endpoint).length?effectiveDomains(endpoint):["Без домена"])){
      const rename=Object.entries(S.G.domain_changes||{}).find(([oldName,v])=>v?.name===d);
      const plannedDomain=(S.G.planned_domains||[]).find(item=>item.name===d);
      const did=plannedDomain?`@domain-plan:${plannedDomain.id}`:`@domain:${d}`;
      if(!domainEntries.has(did))domainEntries.set(did,{label:d,rename,plannedDomain,methods:new Map()});
      const entry=domainEntries.get(did);
      if(!entry.methods.has(method))entry.methods.set(method,[]);
      entry.methods.get(method).push(endpoint);
    }
  }
  // Полка «Без домена» существует всегда — на неё можно вернуть эндпоинт.
  if(!domainEntries.has("@domain:Без домена"))domainEntries.set("@domain:Без домена",{label:"Без домена",rename:null,plannedDomain:null,methods:new Map()});
  for(const entry of domainEntries.values()){
    const items=[...entry.methods.values()].flat();
    entry.plannedGroup=!!entry.rename||!!entry.plannedDomain||(items.length>0&&items.every(n=>n.planned));
  }
  const domainKeys=[...domainEntries.keys()].sort((a,b)=>{
    const la=domainEntries.get(a).label,lb=domainEntries.get(b).label;
    return ((la==="Без домена")?1:0)-((lb==="Без домена")?1:0)||la.localeCompare(lb,"ru");
  });
  for(const did of domainKeys){
    const entry=domainEntries.get(did);
    nodes.push({id:did,viewId:did,kind:"domain",label:entry.label,synthetic:true,planned:entry.plannedGroup,sourceDomain:entry.rename?.[0]||(S.G.domain_changes?.[entry.label]?entry.label:""),domainPlanId:entry.plannedDomain?.id||"",domainRecord:entry.plannedDomain||null,details:{count:entry.methods.size}});
    for(const method of [...entry.methods.keys()].sort((a,b)=>a.localeCompare(b,"ru"))){
      const items=entry.methods.get(method);
      const mid=`@method:${did}:${method}`;
      nodes.push({id:mid,viewId:mid,kind:"method",label:method,synthetic:true,planned:false,domain:entry.label,details:{count:items.length}});
      edges.push({from_node:did,to_node:mid,kind:"uses",label:"метод",planned:entry.plannedGroup});
      for(const item of items)edges.push({from_node:mid,to_node:item.id,kind:"uses",label:"эндпоинт",planned:!!item.planned});
    }
    if(S.mapMode==="fact")for(const [source,change] of Object.entries(S.G.domain_changes||{})){
      const sourceDid=`@domain:${source}`;
      if(!domainEntries.has(sourceDid)||did!==sourceDid)continue;
      const id=`@domain-change:${source}`;
      nodes.push({id,viewId:id,kind:"domain",label:change.name||source,synthetic:true,planned:true,sourceDomain:source,details:{count:0}});
      edges.push({from_node:did,to_node:id,kind:"uses",label:"план переименования",planned:true});
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
  if(pendingChild){
    const parentEntry=viewNodes.find(n=>n.id===pendingChild.parentId);
    if(!parentEntry)pendingChild=null;
    else{
      const newViewId=`@new:${parentEntry.viewId}`;
      viewNodes.push({id:"@new",viewId:newViewId,kind:"new",label:"Новый узел",synthetic:false,pending:true,planned:true,parentId:pendingChild.parentId,branchId:parentEntry.branchId||""});
      viewEdges.push({from_node:parentEntry.viewId,to_node:newViewId,kind:"uses",label:"",planned:true});
    }
  }
  return {nodes:viewNodes,edges:viewEdges,selectedViews};
}
function visibleClass(node){
  if(node.pending)return "";
  if(S.mapMode==="fact"&&(node.planned||node.planned_change&&Object.keys(node.planned_change).length))return " faded";
  if(S.mapMode==="plan"&&!node.planned&&!node.synthetic)return " faded";
  return "";
}
function positionGraph(nodes,edges){
  // Иерархия карты изменилась (домен → метод → эндпоинт) — старые сохранённые
  // позиции больше не соответствуют структуре: один раз за сессию сбрасываем.
  if(!staleLayoutPurged){staleLayoutPurged=true;S.mapPos.clear()}
  const byView=Object.fromEntries(nodes.map(n=>[n.viewId,n])),children=new Map(),parents=new Map();
  const heightOf=id=>nodeAutoHeight(byView[id]);
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
      const d=depth.get(id)||0,nodeWidth=byView[id]?.pending?NEW_W:Math.max(154,250-d*14),kids=treeChildren.get(id)||[];
      widths.set(id,nodeWidth);subtreeWidth.set(id,Math.max(nodeWidth,kids.reduce((sum,child)=>sum+subtreeWidth.get(child),0)+Math.max(0,kids.length-1)*44));
      subtreeHeight.set(id,heightOf(id)+(kids.length?GAP_Y+Math.max(...kids.map(child=>subtreeHeight.get(child))):0));
    };
    measureTree(root);
    const place=(id,left,top)=>{
      const nodeWidth=widths.get(id),span=subtreeWidth.get(id);positions.set(id,{x:left+(span-nodeWidth)/2,y:top});
      const kids=treeChildren.get(id)||[];let kidLeft=left,childTop=top+heightOf(id)+GAP_Y;
      for(const child of kids){place(child,kidLeft,childTop);kidLeft+=subtreeWidth.get(child)+44}
    };
    place(root,x,y);
    return {width:subtreeWidth.get(root)||250,height:subtreeHeight.get(root)||H,treeChildren};
  };
  const endpointBranches=new Map();
  for(const n of nodes)if(n.branchId){if(!endpointBranches.has(n.branchId))endpointBranches.set(n.branchId,[]);endpointBranches.get(n.branchId).push(n)}
  // Кластеры по доменам: домен → его методы → ветки эндпоинтов этих методов.
  const clusters=[];
  const assignedBranches=new Set();
  // Основной домен эндпоинта — первый из его доменов; только он владеет веткой.
  const primaryDomain=new Map();
  for(const endpoint of nodes.filter(n=>n.kind==="endpoint")){
    const domains=String(endpoint.domain||"").split(",").map(v=>v.trim()).filter(Boolean).map(d=>S.mapMode==="plan"&&S.G.domain_changes?.[d]?.name||d);
    const d=domains[0]||"Без домена";
    const plannedDomain=(S.G.planned_domains||[]).find(item=>item.name===d);
    primaryDomain.set(endpoint.id,plannedDomain?`@domain-plan:${plannedDomain.id}`:`@domain:${d}`);
  }
  for(const domain of nodes.filter(n=>n.kind==="domain")){
    const ids=[domain.viewId],treeEdges=new Map(ids.map(id=>[id,[]]));
    const branchIds=[];
    for(const edge of edges){
      if(edge.from_node!==domain.viewId)continue;
      const target=byView[edge.to_node];
      if(!target||ids.includes(edge.to_node))continue;
      ids.push(edge.to_node);treeEdges.set(edge.to_node,[]);
      treeEdges.get(domain.viewId).push(edge.to_node);
      if(target.kind==="method"){
        for(const child of edges.filter(e=>e.from_node===edge.to_node)){
          const branchRoot=byView[child.to_node];
          if(branchRoot?.branchId){
            treeEdges.get(edge.to_node).push(child.to_node);
            if(!branchIds.includes(branchRoot.branchId))branchIds.push(branchRoot.branchId);
          }
        }
      }
    }
    for(const branchId of branchIds){
      if(assignedBranches.has(branchId)||primaryDomain.get(branchId)!==domain.viewId)continue;
      assignedBranches.add(branchId);
      for(const n of endpointBranches.get(branchId)||[])if(!ids.includes(n.viewId)){ids.push(n.viewId);treeEdges.set(n.viewId,[])}
    }
    const idSet=new Set(ids);
    edges.filter(e=>idSet.has(e.from_node)&&idSet.has(e.to_node)).forEach(e=>{
      if(treeEdges.has(e.from_node)&&!treeEdges.get(e.from_node).includes(e.to_node))treeEdges.get(e.from_node).push(e.to_node);
    });
    clusters.push({root:domain.viewId,ids,treeEdges,label:domain.label,domain:String(domain.domain||domain.label)});
  }
  clusters.sort((a,b)=>{
    const sa=a.label==="Без домена",sb=b.label==="Без домена";
    return (sa?1:0)-(sb?1:0)||a.label.localeCompare(b.label,"ru");
  });
  let branchY=100,rowX=44,rowWidth=0,rowHeight=0;const maxRowWidth=1700;
  for(const cluster of clusters){
    const measured=layoutTree(cluster.root,cluster.ids,cluster.treeEdges,0,0);
    if(rowX>44&&rowWidth+measured.width>maxRowWidth){branchY+=rowHeight+110;rowX=44;rowWidth=0;rowHeight=0}
    layoutTree(cluster.root,cluster.ids,cluster.treeEdges,rowX,branchY);
    rowX+=measured.width+110;rowWidth+=measured.width+110;rowHeight=Math.max(rowHeight,measured.height);
  }
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
  for(const n of [...nodes].sort((a,b)=>(positions.get(a.viewId)||{x:0,y:0}).y-(positions.get(b.viewId)||{x:0,y:0}).y||(positions.get(a.viewId)||{x:0}).x-(positions.get(b.viewId)||{x:0}).x)){
    const p=positions.get(n.viewId);
    if(!p){console.warn("mapview: узел без позиции пропущен",n.viewId);continue}
    const w=widths.get(n.viewId)||W,selfH=heightOf(n.viewId);
    while(placed.some(q=>p.x<q.x+q.w+12&&p.x+w+12>q.x&&p.y<q.y+(q.h||H)+12&&p.y+selfH+12>q.y))p.y+=H+12;
    placed.push({x:p.x,y:p.y,w,h:selfH});
  }
  return {positions,widths,children,parents};
}
function draw(){
  const stage=$("mapStage"),svg=$("mapEdges");if(!stage||!svg)return;
  clearStage(stage,svg);const graph=model(),{nodes,edges,selectedViews}=graph,{positions,widths,children,parents}=positionGraph(nodes,edges),byView=Object.fromEntries(nodes.map(n=>[n.viewId,n]));
  const heightOf=id=>nodeAutoHeight(byView[id]);
  if(S.selectedNode&&!selectedViews[S.selectedNode]){
    const previous=S.mapViewNodes?.[S.selectedNode],source=previous?.sourceDomain;
    if(source){const match=Object.values(selectedViews).find(n=>n.sourceDomain===source&&(S.mapMode==="plan"?n.planned:!n.planned));if(match)S.selectedNode=match.id}
    if(String(S.selectedNode).startsWith("@change:"))S.selectedNode=null;
  }
  S.mapViewNodes=selectedViews;
  /* Выделение ветки: выбранный узел и все его потомки подсвечиваются, остальные приглушаются */
  let highlightSet=null;
  if(S.selectedNode){
    const selEntry=selectedViews[S.selectedNode];
    if(selEntry&&!selEntry.pending){
      highlightSet=new Set([selEntry.viewId]);
      const queue=[selEntry.viewId];
      while(queue.length)for(const child of children.get(queue.shift())||[])if(!highlightSet.has(child)){highlightSet.add(child);queue.push(child)}
    }
  }
  let dropTargetId=null,dropTargetEl=null;
  const clearDropTarget=()=>{if(dropTargetEl){dropTargetEl.classList.remove("drop-target");dropTargetEl=null}dropTargetId=null};
  const filtered=nodes.filter(n=>!S.lastSearchQuery||matchesAll(S.lastSearchQuery,[n.label,n.domain,n.description,n.file_path,n.kind,nodeMeta(n),n.planned_change?.note,n.plannedChange?.note,...(n.planned_change?.acceptance||[]),...(n.plannedChange?.acceptance||[])]));
  const shown=new Set(filtered.map(n=>n.viewId));
  // Keep every ancestor so a search result stays inside its endpoint branch.
  let grew=true;while(grew){grew=false;for(const e of edges)if(shown.has(e.to_node)&&byView[e.from_node]&&!shown.has(e.from_node)){shown.add(e.from_node);grew=true}}
  if(!shown.size){const empty=document.createElement("div");empty.className="empty";empty.textContent="Совпадений нет. Очистите поиск.";stage.appendChild(empty)}
  nodes.filter(n=>shown.has(n.viewId)).forEach(n=>{
    const p=positions.get(n.viewId)||{x:44,y:42};
    if(n.pending){
      const card=document.createElement("article");card.className="card map-node pending-node";card.dataset.id=n.viewId;card.dataset.nodeId=n.id;card.style.left=`${p.x}px`;card.style.top=`${p.y}px`;card.style.width=`${NEW_W}px`;card.style.height=`${NEW_H}px`;card.style.overflow="auto";
      const parent=S.byId[n.parentId],kinds=(parent&&CHILD_KINDS[parent.kind])||["nested"];
      card.innerHTML=`<div class="hd"><span class="grip" title="Перетащить узел">⠿</span><span class="chip">новый узел</span><span class="t">Неопределённый узел</span><button type="button" class="pending-close" aria-label="Отменить создание" title="Отменить создание (Esc)">×</button></div><form class="pending-form"><label>Тип узла<select name="kind">${kinds.map(k=>`<option value="${k}">${esc(KIND_NAMES[k]||k)}</option>`).join("")}</select></label><label>Название<input name="name" required maxlength="180" placeholder="Например: Проверить права доступа"></label><label>Задача<textarea name="description" rows="2" placeholder="Что должен делать узел"></textarea></label><label>Критерии приёмки<textarea name="acceptance" rows="2" placeholder="По одному на строку"></textarea></label><div class="pending-actions"><button type="submit" class="primary">Создать</button><button type="button" class="pending-cancel">Отмена</button></div></form>`;
      card.addEventListener("click",ev=>{if(ev.target.closest("button,.grip,input,select,textarea"))return;card.querySelector("input[name=name]")?.focus({preventScroll:true})});
      card.querySelector(".pending-close").addEventListener("click",ev=>{ev.stopPropagation();closePending();draw()});
      card.querySelector(".pending-cancel").addEventListener("click",ev=>{ev.stopPropagation();closePending();draw()});
      card.querySelector("form").addEventListener("submit",async ev=>{
        ev.preventDefault();
        const form=ev.currentTarget,v=new FormData(form),kind=String(v.get("kind")||""),parentKind=parent?.kind;
        const relation=relationFor(parentKind,kind);
        const submitButton=form.querySelector('button[type=submit]');if(submitButton)submitButton.disabled=true;
        try{
          const d=await api("/api/metadata/graph-node","POST",{kind,name:v.get("name"),description:v.get("description"),acceptance:String(v.get("acceptance")||"").split(/\r?\n/).filter(Boolean),domain:parentKind==="endpoint"?parent.domain||"":"",parent_id:n.parentId,edge_kind:relation});
          applyGraph(d.graph);S.selectedNode=d.node_id;closePending();draw();renderInspector();toast("Плановый узел добавлен");
        }catch(error){toast("Не удалось добавить: "+error.message);if(submitButton)submitButton.disabled=false}
      });
      stage.appendChild(card);
      let currentDx=0,currentDy=0;
      const stationaryRects=nodes.filter(other=>other.viewId!==n.viewId).map(other=>{const p0=positions.get(other.viewId);return{x:p0.x,y:p0.y,w:widths.get(other.viewId)||W,h:heightOf(other.viewId)}});
      enableNodeDrag(card,"map",n.viewId,p.x,p.y,S.mapView,(nx,ny)=>{
        const requestedDx=nx-p.x,requestedDy=ny-p.y;
        const delta=clampGroupDelta([{x:p.x,y:p.y,w:NEW_W,h:NEW_H}],stationaryRects,{x:currentDx,y:currentDy},{x:requestedDx,y:requestedDy});
        currentDx=delta.x;currentDy=delta.y;
        const el=stage.querySelector(`[data-id="${CSS.escape(n.viewId)}"]`);
        if(el){el.style.left=`${p.x+delta.x}px`;el.style.top=`${p.y+delta.y}px`}S.mapPos.set(n.viewId,{x:p.x+delta.x,y:p.y+delta.y});S.justDraggedAt=Date.now();
        const liveBoxes=measure(stage);S.mapBoxes=liveBoxes;drawEdges(svg,liveBoxes,edges.filter(e=>shown.has(e.from_node)&&shown.has(e.to_node)));sizeStage(stage,svg);
      },()=>{schedulePersist();draw()},()=>{
        S.mapPos.delete(n.viewId);draw();
      });
      return;
    }
    const card=document.createElement("article");card.className=`card map-node k-${n.kind}${n.planned?" planned node--todo":""}${visibleClass(n)}${S.selectedNode===n.id?" sel":""}${n.synthetic?" synthetic":""}${highlightSet?(highlightSet.has(n.viewId)?" kin":" dim"):""}`;card.dataset.id=n.viewId;card.dataset.nodeId=n.id;card.style.left=`${p.x}px`;card.style.top=`${p.y}px`;card.style.width=`${widths.get(n.viewId)||W}px`;card.style.height=`${heightOf(n.viewId)}px`;
    const kind=n.kind==="method"?"HTTP-метод":n.kind==="domain"?"домен":n.kind==="change"?"план правки":KIND_NAMES[n.kind]||n.kind;
    const detail=n.kind==="endpoint"?`${methodOfEndpoint(n)} ${urlOfEndpoint(n)}\n${(n.details||{}).function_name||""}`:n.kind==="change"?S.byId[n.sourceNodeId]?.label||"фактический узел":n.synthetic?`${n.details?.count||0} узлов`:`${nodeMeta(n)||n.file_path+(n.line?":"+n.line:"")}`;
    const note=n.plannedChange?.note;
    const metaBits=[];
    if(!S.hideMeta){
      const description=String(n.description||"").trim();
      const acceptance=(Array.isArray(n.acceptance)?n.acceptance:[]).map(x=>String(x).trim()).filter(Boolean);
      const domain=String(n.domain||"").trim();
      if(description)metaBits.push(description);
      if(acceptance.length)metaBits.push("✓ "+acceptance.join("  ✓ "));
      if(domain)metaBits.push("домен: "+domain);
    }
    card.innerHTML=`<div class="hd"><span class="grip" title="Перетащить узел и потомков этой ветки">⠿</span><span class="chip">${esc(kind)}</span>${n.planned?'<span class="chip todo">план</span>':""}<span class="t">${esc(n.label)}</span>${!n.synthetic&&n.file_path?'<button class="open-editor" type="button" title="Открыть в редакторе">↗</button>':""}</div><div class="mt">${esc(detail)}</div>${metaBits.length?`<div class="map-meta">${esc(metaBits.join("\n"))}</div>`:""}${n.kind==="change"&&note&&!S.hideMeta?`<div class="planned-note">${esc(note)}</div>`:""}${n.kind!=="change"?'<button type="button" class="map-add-child" aria-label="Добавить потомка" title="Добавить потомка">＋</button>':""}<div class="node-vresize" title="Потянуть за край — высота узла; двойной клик — вернуть автоматически"></div>`;
    card.onclick=ev=>{if(ev.target.closest("button,.grip"))return;ev.stopPropagation();if(Date.now()-S.justDraggedAt<250)return;S.selectedNode=n.id;renderInspector();draw();schedulePersist()};
    card.tabIndex=0;card.setAttribute("role","button");card.setAttribute("aria-label",`${kind}: ${n.label}`);card.addEventListener("keydown",ev=>{if(ev.target!==card)return;if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();S.selectedNode=n.id;renderInspector();draw();schedulePersist()}});
    card.querySelector(".map-add-child")?.addEventListener("click",ev=>{
      ev.stopPropagation();
      if(n.kind==="method"){S.selectedNode="__new_domain__";renderInspector();draw();return}
      if(n.kind==="domain"){S.selectedNode=n.id;renderInspector();$("domainAddEndpoint")?.click();draw();document.querySelector("#graphNodeCreate input[name=name]")?.focus();return}
      closePending();
      pendingChild={parentId:n.id};
      S.selectedNode=n.id;renderInspector();draw();
      document.querySelector(".pending-node input[name=name]")?.focus({preventScroll:true});
    });
    card.querySelector(".open-editor")?.addEventListener("click",ev=>{ev.stopPropagation();openEditorForNode(S.byId[n.id])});
    card.querySelector(".node-vresize")?.addEventListener("pointerdown",ev=>{
      ev.stopPropagation();ev.preventDefault();
      const startY=ev.clientY,startH=heightOf(n.viewId);
      const target=ev.currentTarget;
      try{target.setPointerCapture(ev.pointerId)}catch(_){ }
      card.classList.add("vresizing");
      const onMove=me=>{
        const h=Math.max(MIN_H,Math.min(MAX_H,Math.round(startH+me.clientY-startY)));
        card.style.height=`${h}px`;S.mapH.set(n.id,h);S.justDraggedAt=Date.now();
        const liveBoxes=measure(stage);S.mapBoxes=liveBoxes;drawEdges(svg,liveBoxes,edges.filter(e2=>shown.has(e2.from_node)&&shown.has(e2.to_node)));sizeStage(stage,svg);
      };
      const onUp=()=>{
        target.removeEventListener("pointermove",onMove);target.removeEventListener("pointerup",onUp);target.removeEventListener("pointercancel",onUp);
        card.classList.remove("vresizing");
        if(startH!==heightOf(n.viewId)){schedulePersist();draw()}
      };
      target.addEventListener("pointermove",onMove);
      target.addEventListener("pointerup",onUp);
      target.addEventListener("pointercancel",onUp);
    });
    card.querySelector(".node-vresize")?.addEventListener("dblclick",ev=>{ev.stopPropagation();if(S.mapH.delete(n.id)){schedulePersist();draw();toast("Высота узла — автоматически")}});
    stage.appendChild(card);
    let currentDx=0,currentDy=0;
    const dragOriginals=new Map();
    enableNodeDrag(card,"map",n.viewId,p.x,p.y,S.mapView,(nx,ny)=>{
      const requestedDx=nx-p.x,requestedDy=ny-p.y,moving=new Set([n.viewId]),todo=[n.viewId];
      while(todo.length){for(const c of children.get(todo.pop())||[])if(!moving.has(c)){moving.add(c);todo.push(c)}}
      // Перетаскивание эндпоинта: подсвечиваем карточку метода/домена под курсором.
      let hoverId=null;
      const canDropEndpoint=n.kind==="endpoint";
      const isStudioScenarioCandidate=nn=>nn.kind==="scenario"&&nn.planned&&String(nn.id).startsWith("studio:")&&!nn.plan;
      const parentIsEndpoint=(parents.get(n.viewId)||[]).some(pid=>byView[pid]?.kind==="endpoint");
      const canDropScenario=isStudioScenarioCandidate(n)&&(!n.branchId||parentIsEndpoint);
      if(canDropEndpoint||canDropScenario){
        const cx=nx+(widths.get(n.viewId)||W)/2,cy=ny+heightOf(n.viewId)/2;
        let hoverDist=150;
        for(const other of nodes){
          if(other===n)continue;
          if(other.kind==="method"){
            if(canDropEndpoint&&other.domain==="Без домена")continue;
          }else if(other.kind==="domain"){
            if(!canDropEndpoint)continue;
          }else continue;
          const box=S.mapBoxes[other.viewId];
          if(!box)continue;
          const dist=Math.hypot(cx-(box.x+box.w/2),cy-(box.y+box.h/2));
          if(dist<hoverDist){hoverDist=dist;hoverId=other.viewId}
        }
      }
      if(hoverId!==dropTargetId){
        clearDropTarget();
        dropTargetId=hoverId;
        dropTargetEl=hoverId?stage.querySelector(`[data-id="${CSS.escape(hoverId)}"]`):null;
        if(dropTargetEl)dropTargetEl.classList.add("drop-target");
      }
      const movingRects=[...moving].map(id=>{const p0=positions.get(id);return{x:p0.x,y:p0.y,w:widths.get(id)||W,h:heightOf(id)}});
      const stationaryRects=nodes.filter(other=>!moving.has(other.viewId)).map(other=>{const p0=positions.get(other.viewId);return{x:p0.x,y:p0.y,w:widths.get(other.viewId)||W,h:heightOf(other.viewId)}});
      const delta=clampGroupDelta(movingRects,stationaryRects,{x:currentDx,y:currentDy},{x:requestedDx,y:requestedDy});
      currentDx=delta.x;currentDy=delta.y;
      const candidates=[...moving].map(id=>({id,x:(positions.get(id)?.x||0)+delta.x,y:(positions.get(id)?.y||0)+delta.y}));
      candidates.forEach(a=>{
        if(!dragOriginals.has(a.id)){const p0=positions.get(a.id);dragOriginals.set(a.id,p0?{x:p0.x,y:p0.y}:null)}
        const el=stage.querySelector(`[data-id="${CSS.escape(a.id)}"]`);if(el){el.style.left=`${a.x}px`;el.style.top=`${a.y}px`}S.mapPos.set(a.id,{x:a.x,y:a.y})
      });S.justDraggedAt=Date.now();
      const liveBoxes=measure(stage);S.mapBoxes=liveBoxes;drawEdges(svg,liveBoxes,edges.filter(e=>shown.has(e.from_node)&&shown.has(e.to_node)));sizeStage(stage,svg);
    },(cancelled)=>{
      if(cancelled){
        for(const [id,orig] of dragOriginals){if(orig)S.mapPos.set(id,orig);else S.mapPos.delete(id)}
        draw();
        return;
      }
      const targetNode=dropTargetId?byView[dropTargetId]:null;
      const newDomain=dropDomainOf(targetNode);
      const isStudioScenario=n.kind==="scenario"&&n.planned&&String(n.id).startsWith("studio:")&&!n.plan;
      const parentIsEndpoint=(parents.get(n.viewId)||[]).some(pid=>byView[pid]?.kind==="endpoint");
      const canDropScenario=isStudioScenario&&(!n.branchId||parentIsEndpoint);
      clearDropTarget();
      if(n.kind==="endpoint"&&newDomain!==null){assignEndpointDomain(n,newDomain);return}
      if(canDropScenario&&targetNode?.kind==="method"){
        const currentParent=(parents.get(n.viewId)||[]).map(pid=>byView[pid]).find(t=>t&&t.kind==="endpoint");
        const currentId=currentParent?(currentParent.canonicalId||currentParent.id):null;
        const choices=[...new Set(edges.filter(e=>e.from_node===targetNode.id).map(e=>byView[e.to_node]).filter(t=>t&&t.branchId).map(t=>t.branchId))]
          .filter(id=>id!==currentId)
          .map(id=>{
            const base=S.byId[id]?.label||id;
            const kids=nodes.filter(nn=>nn.branchId===id&&nn.kind==="scenario").map(nn=>nn.label).slice(0,2).join(", ");
            return {id,label:kids?`${base} · ${kids}`:base};
          });
        attachScenarioToMethod(n,targetNode,choices);
        return;
      }
      schedulePersist();draw();
    },()=>{
      for(const [id,orig] of dragOriginals){if(orig)S.mapPos.set(id,orig);else S.mapPos.delete(id)}
      draw();
    });
  });
  const boxes=measure(stage);S.mapBoxes=boxes;
  const shownEdges=edges.filter(e=>shown.has(e.from_node)&&shown.has(e.to_node)).map(e=>({...e,todo:!!(byView[e.from_node]?.planned||byView[e.to_node]?.planned||e.planned)}));
  drawEdges(svg,boxes,shownEdges);sizeStage(stage,svg);applyTransform("mapView",S.mapView);
  const mode=$("mapModeCaption");if(mode)mode.textContent=S.mapMode==="fact"?"Фактическая структура проекта":"Планируемая структура и изменения";
}
function renderMap(){draw()}
document.addEventListener("keydown",ev=>{
  if(ev.key!=="Escape"||!pendingChild)return;
  if(document.querySelector(".studio-modal"))return;
  closePending();draw();
});
export {renderMap};

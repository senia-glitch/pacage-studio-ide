import {
  $, S, hooks, applyTransform, areaSize, cardHtml, drawEdges, enableNodeDrag,
  enableZoneDrag, esc, methodOfEndpoint, nestedOf,
  notesOf, onCardClick, openEditorForNode, nestedLayoutKey, planLineHtml,
  proposedOverlap, schedulePersist, styleRect, todoBadgeHtml, zoneOverlaps,
  KIND_NAMES, METHOD_COLORS, NO_DOMAIN, NO_METHOD, ROLE_NAMES,
} from "./core.js";
import {clearStage, measure, sizeStage} from "./edges.js";
import {methodOrder, proposedZoneRect} from "./geometry.js";
import {matchesAll} from "./search.js";
import {isTodo, placeNode} from "./todo.js";
import {revealElement} from "./viewport.js";

function makeCard(n,x,y,opts={},parent=null,scope=S.selectedScenario){
  const key=`${scope}|${n.id}`;
  const saved=S.scenPos.get(key);
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
  e.querySelectorAll(".nested-item").forEach(button=>{
    button.onclick=ev=>{ev.stopPropagation();focusIncompleteNode(button.dataset.nested)};
  });
  (parent||$("scenStage")).appendChild(e);
  enableNodeDrag(e,"scen",key,x,y,S.scenView,
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
  const nestedNodes=[...zone.querySelectorAll(".nested-node")].filter(pc=>pc.dataset.parentId===card.dataset.id);
  for(const pc of nestedNodes){
    const px=(parseFloat(pc.style.left)||0)+dx,py=(parseFloat(pc.style.top)||0)+dy;
    if(px<14||py<34)return;
    moving.push(pc);proposed.push({el:pc,x:px,y:py});
  }
  if(proposedOverlap(zone,moving,proposed))return;
  const overrides=new Map(proposed.map(p=>[p.el,{x:p.x,y:p.y}]));
  if(zoneOverlaps(zone,proposedZoneRect(zone,areaSize(zone,overrides)),scenZones()))return;
  card.style.left=cx+"px";card.style.top=cy+"px";
  S.scenPos.set(key,{x:cx,y:cy});
  nestedNodes.forEach((pc,index)=>{
    const p=proposed[index+1];
    pc.style.left=p.x+"px";pc.style.top=p.y+"px";
    S.nestedPos.set(nestedLayoutKey(scope,pc.dataset.id),{x:p.x,y:p.y});
  });
  recomputeScenarioSizes();redrawScenarioEdges();
}

function scenZones(){return[...$("scenStage").querySelectorAll(".scen-zone")]}
function incompleteNodes(){
  return (S.G.nodes||[]).filter(n=>placeNode(n,S.G).area==="offpath");
}

function endpointsOfScenario(id){
  const path=S.G.scenario_paths&&S.G.scenario_paths[id];
  const ids=(path&&path.endpoints&&path.endpoints.length)?path.endpoints:[];
  if(ids.length)return ids.filter(ep=>S.byId[ep]);
  return (S.G.edges||[]).filter(e=>e.kind==="dispatch"&&e.to_node===id&&S.byId[e.from_node]).map(e=>e.from_node);
}
function methodsOfScenario(id){
  const methods=new Set();
  endpointsOfScenario(id).forEach(epId=>{
    const ep=S.byId[epId];
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
  const scenarios=(S.G.scenarios||[]).filter(id=>{
    const n=S.byId[id];if(!n)return false;
    return !S.lastSearchQuery||matchesAll(S.lastSearchQuery,[n.label,n.domain,n.description,n.file_path]);
  });
  scenarios.forEach(id=>{const n=S.byId[id];if(!n)return;
    const o=document.createElement("option");o.value=id;o.textContent=n.label+(isTodo(n)?" [TODO]":"");o.selected=id===S.selectedScenario;sel.appendChild(o)});
  sel.onchange=()=>{S.selectedScenario=sel.value;S.selectedNode=null;hooks.renderScenario();hooks.renderInspector()};
  const list=$("scenList");
  list.querySelectorAll("details[data-key]").forEach(d=>S.listOpen.set(d.dataset.key,d.open));
  list.innerHTML="";
  if(!scenarios.length){
    const env=(S.studioInfo&&S.studioInfo.env)||{};
    list.innerHTML=S.lastSearchQuery
      ?'<p class="hint" style="padding:6px">Ничего не найдено.</p>'
      :`<p class="hint" style="padding:6px">Сценариев нет.<br>Проверено: ${esc(env.core_discover||"app.scenarios")}<br>`+
        `Поправьте FAT_CORE_DISCOVER или выполните <b>studio doctor</b>.</p>`;
    return;
  }

  const groups=new Map();
  scenarios.forEach(id=>{
    const n=S.byId[id];if(!n)return;
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
    mDet.open=S.listOpen.has(mDet.dataset.key)?S.listOpen.get(mDet.dataset.key):true;
    const mSum=document.createElement("summary");
    mSum.innerHTML=`<span class="method-dot" style="background:${METHOD_COLORS[method]||"#9a9aa2"}"></span>${esc(method)} <span class="hint">· ${total}</span>`;
    mDet.appendChild(mSum);
    [...byDomain.keys()].sort(domainOrder).forEach(domain=>{
      const items=byDomain.get(domain);
      const dDet=document.createElement("details");
      dDet.dataset.key="d:"+method+":"+domain;dDet.dataset.level="domain";
      dDet.open=S.listOpen.has(dDet.dataset.key)?S.listOpen.get(dDet.dataset.key):true;
      const dSum=document.createElement("summary");
      dSum.innerHTML=`${esc(domain)} <span class="hint">· ${items.length}</span>`;
      dDet.appendChild(dSum);
      items.forEach(id=>{
        const n=S.byId[id];if(!n)return;
        const e=document.createElement("div");
        e.className="sitem"+(S.selectedScenario===id?" on":"");e.title=n.id;
        e.innerHTML=`${esc(n.label)}${todoBadgeHtml(n)}`;
        e.onclick=()=>{S.selectedScenario=id;S.selectedNode=null;hooks.renderScenario();hooks.renderInspector()};
        dDet.appendChild(e);
      });
      mDet.appendChild(dDet);
    });
    list.appendChild(mDet);
  });
  const incomplete=incompleteNodes();
  const incDet=document.createElement("details");
  incDet.dataset.key="incomplete";incDet.dataset.level="method";
  incDet.open=S.listOpen.has("incomplete")?S.listOpen.get("incomplete"):true;
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
    e.innerHTML=`<span class="hint">${esc(KIND_NAMES[n.kind]||n.kind)}</span> · ${esc(n.label)} ${todoBadgeHtml(n)}`;
    e.onclick=()=>focusIncompleteNode(n.id);
    incDet.appendChild(e);
  });
  list.appendChild(incDet);
}
function focusIncompleteNode(id){
  S.selectedNode=id;hooks.renderScenario();hooks.renderInspector();
  const el=[...document.querySelectorAll("#scenStage .card[data-id]")].find(e=>e.dataset.id===id);
  if(el)revealElement($("scenCanvas"),S.scenView,el,()=>applyTransform("scenView",S.scenView));
}
function scenarioEdges(ids){
  const set=new Set(ids);
  const mark=edge=>({...edge,todo:!!(S.byId[edge.from_node]?.planned||S.byId[edge.to_node]?.planned),yellow:!!(edge.notes&&edge.notes.length)});
  return (S.G.edges||[]).filter(e=>set.has(e.from_node)&&set.has(e.to_node)).map(mark);
}
function redrawScenarioEdges(){
  const stage=$("scenStage"),svg=$("scenEdges");
  S.scenBoxes=measure(stage);
  drawEdges(svg,S.scenBoxes,scenarioEdges(Object.keys(S.scenBoxes)));
}
function recomputeScenarioSizes(){
  const stage=$("scenStage"),svg=$("scenEdges");
  stage.querySelectorAll(".scen-zone").forEach(zone=>{
    const size=areaSize(zone);
    zone.style.width=size.w+"px";zone.style.height=size.h+"px";
  });
  sizeStage(stage,svg);
}
function renderNested(zone,parentCard,node,scope){
  if(S.hideNested)return;
  let cursor=(parseFloat(parentCard.style.top)||0)+parentCard.offsetHeight+10;
  const left=(parseFloat(parentCard.style.left)||0)+18;
  nestedOf(node.id).forEach((item,index)=>{
    const key=nestedLayoutKey(scope,item.id);
    const saved=S.nestedPos.get(key);
    const x=saved?saved.x:left;
    const y=saved?saved.y:cursor;
    const card=makeCard(item,x,y,{nested:true},zone,scope);
    card.classList.add("nested-node");
    card.dataset.parentId=node.id;
    card.title=item.id;
    cursor=Math.max(cursor,(parseFloat(card.style.top)||y)+card.offsetHeight+8);
  });
}
function renderScenario(){
  fillScenarioSelect();
  const stage=$("scenStage"),svg=$("scenEdges");
  clearStage(stage,svg);S.scenBoxes={};
  if(!S.G.scenarios.length){
    const env=(S.studioInfo&&S.studioInfo.env)||{};
    const e=document.createElement("div");e.className="empty";e.innerHTML=
      `Сценариев core-package не найдено.<br>`+
      `<span class="hint">Проверено: ${esc(env.core_discover||"app.scenarios")} · `+
      `эндпоинты: ${esc(env.routes_package||"app.api.v1.routes")} · модели: ${esc(env.models||"models.py")}</span><br>`+
      `Поправьте FAT_CORE_DISCOVER в .env.fastbase или выполните <b>studio doctor</b>.`;
    stage.appendChild(e);
    stage.style.width="900px";stage.style.height="500px";applyTransform("scenView",S.scenView);hooks.renderInspector();return;
  }
  if(!S.selectedScenario)S.selectedScenario=S.G.scenarios[0];
  const path=S.G.scenario_paths[S.selectedScenario];
  if(!path){
    const e=document.createElement("div");e.className="empty";e.innerHTML=
      "У этого сценария нет пути от эндпоинта.<br><span class=\"hint\">Проверьте run(\"…\") в эндпоинте и @register_scenario. Узел остаётся в «Неполных».</span>";
    stage.appendChild(e);applyTransform("scenView",S.scenView);return;
  }
  const defs=[["endpoint","эндпоинт",30],["scenario","сценарий",380],["io","io",730],["model","модель",1080],["tests","тесты",1430],["incomplete","неполные",30]];
  const layers={};
  defs.forEach(([kind,title,defaultX])=>{
    const zone=document.createElement("div");
    zone.className="zone scen-zone";zone.dataset.layer=kind;
    const key=kind==="incomplete"?"incomplete|zone":`${S.selectedScenario}|zone|${kind}`;
    let pos=S.scenZonePos.get(key);
    if(!pos){pos={x:defaultX,y:kind==="incomplete"?620:40};S.scenZonePos.set(key,pos)}
    zone.style.left=pos.x+"px";zone.style.top=pos.y+"px";
    zone.style.width="240px";zone.style.height="70px";
    const lab=document.createElement("div");lab.className="zlab";
    lab.innerHTML=`<span class="grip" title="перетащить">⠿</span>${esc(title)} <span class="hint">· 0</span>`;
    zone.appendChild(lab);stage.appendChild(zone);layers[kind]=zone;
    enableZoneDrag(zone,S.scenView,
      (nx,ny)=>{
        const cx=Math.max(10,nx),cy=Math.max(10,ny);
        const rect={x:cx,y:cy,w:zone.offsetWidth,h:zone.offsetHeight};
        if(zoneOverlaps(zone,rect,scenZones()))return;
        zone.style.left=cx+"px";zone.style.top=cy+"px";
        S.scenZonePos.set(key,{x:cx,y:cy});
        if(kind==="incomplete")S.incompleteZoneMoved=true;
        redrawScenarioEdges();
      },
      ()=>{
        S.scenZonePos.set(key,{x:parseFloat(zone.style.left)||0,y:parseFloat(zone.style.top)||0});
        recomputeScenarioSizes();redrawScenarioEdges();
      });
  });
  const nextY={};
  const add=(id,layer,x,y,opts,scope)=>{
    if(!id||!S.byId[id])return null;
    const card=makeCard(S.byId[id],x,y,opts,layers[layer],scope);
    renderNested(layers[layer],card,S.byId[id],scope);
    return card;
  };
  const stack=(id,layer,opts)=>{
    const y=nextY[layer]===undefined?38:nextY[layer];
    const card=add(id,layer,14,y,opts,S.selectedScenario);
    if(card){
      let bottom=(parseFloat(card.style.top)||y)+card.offsetHeight;
      layers[layer].querySelectorAll(".nested-node").forEach(pc=>{
        if(pc.dataset.parentId===card.dataset.id)bottom=Math.max(bottom,(parseFloat(pc.style.top)||0)+pc.offsetHeight);
      });
      nextY[layer]=bottom+12;
    }
    return card;
  };
  if(path.endpoint)stack(path.endpoint,"endpoint",{});
  else{
    const note=document.createElement("div");note.className="hint";note.style.position="absolute";note.style.left="14px";note.style.top="42px";
    note.textContent="нет эндпоинта — узел в «неполных», но цепочка показана";layers.endpoint.appendChild(note);
  }
  stack(path.scenario,"scenario",{});
  (path.called_scenarios||[]).forEach(id=>{if(id!==S.selectedScenario)stack(id,"scenario",{nested:true})});
  (path.io_calls||[]).forEach(id=>stack(id,"io",{}));
  (path.models||[]).forEach(id=>stack(id,"model",{}));
  (path.tests||[]).forEach(id=>stack(id,"tests",{}));
  const shown=new Set([path.endpoint,path.scenario,...(path.called_scenarios||[]),...(path.io_calls||[]),...(path.models||[]),...(path.tests||[])].filter(Boolean));
  (S.G.edges||[])
    .filter(edge=>edge.from_node===S.selectedScenario||edge.to_node===S.selectedScenario)
    .forEach(edge=>{
      const otherId=edge.from_node===S.selectedScenario?edge.to_node:edge.from_node;
      if(shown.has(otherId)||!S.byId[otherId])return;
      const other=S.byId[otherId];
      const layer=other.kind==="test"?"tests":other.kind;
      if(layers[layer]){stack(otherId,layer,{nested:true});shown.add(otherId)}
    });
  const columnX={scenario:14,io:330,model:646,test:962,endpoint:14};
  const columnY={};
  incompleteNodes()
    .filter(n=>!shown.has(n.id))
    .slice()
    .sort((a,b)=>(KIND_NAMES[a.kind]||"").localeCompare(KIND_NAMES[b.kind]||"")||a.label.localeCompare(b.label))
    .forEach(n=>{
      const layer=layers.incomplete;
      const x=columnX[n.kind]!==undefined?columnX[n.kind]:14;
      const y=columnY[n.kind]===undefined?38:columnY[n.kind];
      const card=add(n.id,"incomplete",x,y,{}, "incomplete");
      let bottom=(parseFloat(card.style.top)||y)+card.offsetHeight;
      layer.querySelectorAll(".nested-node").forEach(pc=>{
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
  if(!S.incompleteZoneMoved){
    let bottom=40;
    defs.forEach(([kind])=>{
      if(kind==="incomplete")return;
      const zone=layers[kind];
      bottom=Math.max(bottom,(parseFloat(zone.style.top)||0)+zone.offsetHeight);
    });
    layers.incomplete.style.left="30px";
    layers.incomplete.style.top=(bottom+40)+"px";
    S.scenZonePos.set("incomplete|zone",{x:30,y:bottom+40});
    recomputeScenarioSizes();
  }
  redrawScenarioEdges();
  applyTransform("scenView",S.scenView);
  hooks.renderInspector();
}


export {renderScenario, redrawScenarioEdges, recomputeScenarioSizes, focusIncompleteNode};







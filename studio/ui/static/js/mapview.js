import {
  $, S, applyTransform, areaSize, drawEdges, enableNodeDrag, enableZoneDrag, esc,
  methodOfEndpoint, nestedListHtml, nodeMeta, nodeTodoClass,
  notesOf, openEditorForNode, openEndpointScenario, planLineHtml, proposedOverlap, renderDomainChips,
  schedulePersist, styleRect, todoBadgeHtml, todoDownstreamHtml, urlOfEndpoint,
  domainOfNode, zoneOverlaps, KIND_NAMES, METHOD_COLORS, NO_DOMAIN,
} from "./core.js";
import {clearStage, measure, sizeStage} from "./edges.js";
import {methodOrder, proposedZoneRect} from "./geometry.js";
import {focusIncompleteNode} from "./scenarioview.js";
import {matchesAll} from "./search.js";
import {OFF_PATH_KINDS, OFF_PATH_TITLES, groupOffPath, isTodo, offPathCount} from "./todo.js";

function mapZones(){return[...$("mapStage").querySelectorAll(".map-method,.map-offpath")]}
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
function offpathCard(zone,n,x,y){
  const card=document.createElement("div");
  card.className=`card k-${n.kind}${nodeTodoClass(n)}${notesOf(n).length?" node--yellow":""}`;card.dataset.id=n.id;
  const saved=S.mapPos.get(n.id);
  const left=saved?saved.x:x,top=saved?saved.y:y;
  card.style.left=left+"px";card.style.top=top+"px";card.title=n.id;
  card.innerHTML=`<div class="hd"><span class="grip" title="перетащить">⠿</span><span class="chip">${esc(KIND_NAMES[n.kind]||n.kind)}</span>${todoBadgeHtml(n)}<span class="t">${esc(n.label)}</span><button class="open-editor" type="button" title="Открыть в VS Code" aria-label="Открыть в VS Code">↗</button></div>`+
    planLineHtml(n)+`<div class="mt">${esc(nodeMeta(n)||"неполный узел")}</div>`+
    (n.description?`<div class="sec"><div class="lbl">${isTodo(n)?"что сделать":"описание"}</div><div class="body">${esc(n.description)}</div></div>`:"");
  card.tabIndex=0;card.setAttribute("role","button");card.setAttribute("aria-label",n.label);
  card.onclick=ev=>{ev.stopPropagation();if(Date.now()-S.justDraggedAt<250)return;focusIncompleteNode(n.id)};
  card.addEventListener("keydown",ev=>{
    if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();focusIncompleteNode(n.id)}
    if(ev.key.toLowerCase()==="o"){ev.preventDefault();openEditorForNode(n)}
  });
  const openBtn=card.querySelector(".open-editor");
  if(openBtn)openBtn.onclick=ev=>{ev.stopPropagation();openEditorForNode(n)};
  zone.appendChild(card);
  enableNodeDrag(card,"map",n.id,left,top,S.mapView,
    (nx,ny)=>{
      const cx=Math.max(14,nx),cy=Math.max(38,ny);
      if(proposedOverlap(zone,[card],[{el:card,x:cx,y:cy}]))return;
      card.style.left=cx+"px";card.style.top=cy+"px";
      S.mapPos.set(n.id,{x:cx,y:cy});recomputeMapSizes();
      sizeStage($("mapStage"),$("mapEdges"));
    },
    ()=>{
      S.mapPos.set(n.id,{x:parseFloat(card.style.left)||0,y:parseFloat(card.style.top)||0});
      recomputeMapSizes();sizeStage($("mapStage"),$("mapEdges"));
    });
  return card;
}
function renderOffPathMap(stage,svg){
  const groups=groupOffPath((S.G&&S.G.nodes)||[]);
  const total=offPathCount(groups);
  if(!total)return;
  const zone=document.createElement("div");
  zone.className="zone map-zone map-offpath";
  zone.style.left=S.mapOffpath.x+"px";zone.style.top=S.mapOffpath.y+"px";
  zone.style.width="280px";zone.style.height="80px";
  const lab=document.createElement("div");lab.className="zlab";
  lab.innerHTML=`<span class="grip" title="перетащить">⠿</span><b>Вне путей запроса (${total})</b>`;
  zone.appendChild(lab);stage.appendChild(zone);
  let cursor=38;
  OFF_PATH_KINDS.forEach(kind=>{
    const items=groups[kind]||[];
    if(!items.length)return;
    const title=document.createElement("div");
    title.className="off-group-title";title.textContent=`${OFF_PATH_TITLES[kind]} · ${items.length}`;
    title.style.top=cursor+"px";zone.appendChild(title);cursor+=18;
    items.forEach(n=>{
      const card=offpathCard(zone,n,14,cursor);
      cursor=Math.max(cursor,(parseFloat(card.style.top)||cursor)+card.offsetHeight+10);
    });
    cursor+=4;
  });
  let width=260;
  zone.querySelectorAll(".card[data-id]").forEach(card=>{
    const r=styleRect(card);width=Math.max(width,r.x+r.w+18);
  });
  zone.style.width=width+"px";zone.style.height=(cursor+8)+"px";
  enableZoneDrag(zone,S.mapView,
    (nx,ny)=>{
      const cx=Math.max(20,nx),cy=Math.max(20,ny);
      const rect={x:cx,y:cy,w:zone.offsetWidth,h:zone.offsetHeight};
      if(zoneOverlaps(zone,rect,mapZones()))return;
      zone.style.left=cx+"px";zone.style.top=cy+"px";
      S.mapOffpath.x=cx;S.mapOffpath.y=cy;sizeStage(stage,svg);
    },
    ()=>{S.mapOffpath.x=parseFloat(zone.style.left)||40;S.mapOffpath.y=parseFloat(zone.style.top)||420;sizeStage(stage,svg)});
  S.mapBoxes=measure(stage);
  const edges=(S.G.edges||[]).filter(e=>S.mapBoxes[e.from_node]&&S.mapBoxes[e.to_node]).map(e=>({
    ...e,
    todo:!!(S.byId[e.from_node]?.planned||S.byId[e.to_node]?.planned),
    yellow:!!(e.notes&&e.notes.length),
  }));
  drawEdges(svg,S.mapBoxes,edges);
  sizeStage(stage,svg);
}
function renderMap(){
  const stage=$("mapStage"),svg=$("mapEdges");
  clearStage(stage,svg);S.mapBoxes={};
  renderDomainChips();
  const allEndpoints=(S.G.nodes||[]).filter(n=>n.kind==="endpoint");
  const endpoints=allEndpoints.filter(n=>{
    if(S.lastSearchQuery&&!matchesAll(S.lastSearchQuery,[n.label,methodOfEndpoint(n),urlOfEndpoint(n),n.domain,n.description,(n.details||{}).function_name]))return false;
    if(!S.domainFilter.size)return true;
    const names=String(n.domain||NO_DOMAIN).split(",").map(s=>s.trim());
    return names.some(name=>S.domainFilter.has(name));
  });
  if(!endpoints.length){
    const e=document.createElement("div");e.className="empty";
    if(allEndpoints.length){
      e.innerHTML=`Ничего не найдено по запросу «${esc(S.lastSearchQuery)}».<br>Очистите поле поиска.`;
    }else{
      const env=(S.studioInfo&&S.studioInfo.env)||{};
      e.innerHTML=`Эндпоинтов fastbase не найдено.<br>`+
        `<span class="hint">Проверено: ${esc(env.routes_package||"app.api.v1.routes")} · `+
        `сценарии: ${esc(env.core_discover||"app.scenarios")} · модели: ${esc(env.models||"models.py")}</span><br>`+
        `Поправьте FAT_ROUTES_PACKAGE в .env.fastbase или выполните <b>studio doctor</b>.`;
    }
    stage.appendChild(e);
    renderOffPathMap(stage,svg);
    if(!stage.querySelector(".map-offpath")){stage.style.width="900px";stage.style.height="500px"}
    applyTransform("mapView",S.mapView);return;
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
  fast.style.left=S.mapFastbase.x+"px";fast.style.top=S.mapFastbase.y+"px";
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
      card.className=`card method-${method.toLowerCase()}${n.planned?" planned":""}${notesOf(n).length?" node--yellow":""}${nodeTodoClass(n)}`;card.dataset.id=n.id;
      const saved=S.mapPos.get(n.id);
      const lx=saved?saved.x:14+(i%2)*292;
      const ly=saved?saved.y:38+Math.floor(i/2)*94;
      card.style.left=lx+"px";card.style.top=ly+"px";
      const url=urlOfEndpoint(n);
      const title=url?`${method} ${url}`:`${method} · ${n.label}`;
      const sub=[url?(d.function_name||""):"URL неизвестен",domainOfNode(n)].filter(Boolean).join(" · ");
      const mapYellow=notesOf(n).length>0;
      card.innerHTML=`<div class="hd"><span class="grip" title="перетащить">⠿</span><span class="chip">${esc(KIND_NAMES[n.kind]||n.kind)}</span>${todoBadgeHtml(n)}${mapYellow?`<span class="chip warn">неточно</span>`:""}${todoDownstreamHtml(n)}<span class="t">${esc(title)}</span><button class="open-editor" type="button" title="Открыть в VS Code" aria-label="Открыть в VS Code">↗</button></div>`+
        planLineHtml(n)+`<div class="mt">${esc(sub)}</div>`+nestedListHtml(n);
      card.tabIndex=0;card.setAttribute("role","button");card.setAttribute("aria-label",title);
      const chainBtn=card.querySelector(".todo-chain");
      if(chainBtn)chainBtn.onclick=ev=>{ev.stopPropagation();openEndpointScenario(n.id)};
      card.onclick=ev=>{ev.stopPropagation();if(Date.now()-S.justDraggedAt<250)return;openEndpointScenario(n.id)};
      card.addEventListener("keydown",ev=>{if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();openEndpointScenario(n.id)}});
      const mapOpen=card.querySelector(".open-editor");
      if(mapOpen)mapOpen.onclick=ev=>{ev.stopPropagation();openEditorForNode(n)};
      card.querySelectorAll(".nested-item").forEach(button=>{
        button.onclick=ev=>{ev.stopPropagation();openEndpointScenario(n.id);S.selectedNode=button.dataset.nested;renderScenario();renderInspector()};
      });
      zone.appendChild(card);
      enableNodeDrag(card,"map",n.id,lx,ly,S.mapView,
        (nx,ny)=>{
          const cx=Math.max(14,nx),cy=Math.max(38,ny);
          const proposed=[{el:card,x:cx,y:cy}];
          if(proposedOverlap(zone,[card],proposed))return;
          const size=areaSize(zone,new Map([[card,{x:cx,y:cy}]]));
          if(zoneOverlaps(zone,proposedZoneRect(zone,size),mapZones()))return;
          card.style.left=cx+"px";card.style.top=cy+"px";
          S.mapPos.set(n.id,{x:cx,y:cy});recomputeMapSizes();
        },
        ()=>{
          S.mapPos.set(n.id,{x:parseFloat(card.style.left)||0,y:parseFloat(card.style.top)||0});
          recomputeMapSizes();
        });
    });
  });
  recomputeMapSizes();
  let cursor=18;
  methods.forEach(method=>{
    const zone=zones[method];
    let pos=S.mapMethods.get(method);
    if(!pos){pos={x:cursor,y:38};S.mapMethods.set(method,pos);cursor+=zone.offsetWidth+28}
    zone.style.left=pos.x+"px";zone.style.top=pos.y+"px";
  });
  recomputeMapSizes();
  methods.forEach(method=>{
    const zone=zones[method];
    enableZoneDrag(zone,S.mapView,
      (nx,ny)=>{
        const cx=Math.max(16,nx),cy=Math.max(38,ny);
        const rect={x:cx,y:cy,w:zone.offsetWidth,h:zone.offsetHeight};
        if(zoneOverlaps(zone,rect,mapZones()))return;
        zone.style.left=cx+"px";zone.style.top=cy+"px";
        S.mapMethods.set(method,{x:cx,y:cy});recomputeMapSizes();
      },
      ()=>{
        S.mapMethods.set(method,{x:parseFloat(zone.style.left)||0,y:parseFloat(zone.style.top)||0});
        recomputeMapSizes();
      });
  });
  enableZoneDrag(fast,S.mapView,
    (nx,ny)=>{
      const cx=Math.max(20,nx),cy=Math.max(20,ny);
      fast.style.left=cx+"px";fast.style.top=cy+"px";
      S.mapFastbase.x=cx;S.mapFastbase.y=cy;
    },
    ()=>{S.mapFastbase.x=parseFloat(fast.style.left)||40;S.mapFastbase.y=parseFloat(fast.style.top)||40;sizeStage(stage,svg)});
  renderOffPathMap(stage,svg);
  sizeStage(stage,svg);
  applyTransform("mapView",S.mapView);
}

export {renderMap};



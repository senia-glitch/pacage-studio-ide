/* Чистые функции доски идей: без DOM и сети, проверяются node-тестами. */

export function id(){return `idea-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`}

export function text(v){return typeof v==="string"?v:""}

export function strings(value){return Array.isArray(value)?value.map(x=>String(x).trim()).filter(Boolean):[]}

export function blockFrom(raw,index){
  const b=raw&&typeof raw==="object"&&!Array.isArray(raw)?raw:{};
  return {id:text(b.id)||id(),name:text(b.name||b.title)||`Блок ${index+1}`,description:text(b.description),acceptance:strings(b.acceptance),calls:strings(b.calls),mapBlockId:text(b.mapBlockId)};
}

export function pageFrom(raw,index){
  const source=raw.source&&typeof raw.source==="object"&&!Array.isArray(raw.source)?raw.source:{...raw};
  return {id:text(raw.id)||id(),source,title:text(raw.title||raw.name)||`Страница ${index+1}`,name:text(raw.name),description:text(raw.description),acceptance:strings(raw.acceptance),domain:text(raw.domain),route:text(raw.route),calls:strings(raw.calls),blocks:(Array.isArray(raw.blocks)?raw.blocks:[]).map(blockFrom),mapPageId:text(raw.mapPageId),decision:text(raw.decision),implemented:raw.implemented===true};
}

export function pagesFrom(list){
  return (Array.isArray(list)?list:[]).filter(item=>item&&typeof item==="object"&&!Array.isArray(item)).map(pageFrom);
}

export function pageSource(page){
  return {...page.source,title:page.title,name:page.name,description:page.description,acceptance:page.acceptance,domain:page.domain,route:page.route,calls:page.calls,blocks:page.blocks.map(b=>({name:b.name,description:b.description,acceptance:b.acceptance,calls:b.calls}))};
}

export function plural(n,one,few,many){const a=n%10,b=n%100;return a===1&&b!==11?one:(a>=2&&a<=4&&(b<10||b>=20)?few:many)}

export function railCounts(idea){
  const s=idea.scenarios.length,p=idea.pages.length;
  return `${s} ${plural(s,"сценарий","сценария","сценариев")}`+(p?` · ${p} ${plural(p,"страница","страницы","страниц")}`:"");
}

export function railLabel(idea){return railCounts(idea)+(idea.created_by==="agent"?" · от агента":"")}

/* ---------- группы идей в левой панели ---------- */

/* Карточка (сценарий или страница) реализована: её пометили при приёмке или узел на карте уже не плановый. */
export function isCardDone(card,findNode){
  if(!card)return false;
  if(card.implemented===true)return true;
  const node=card.mapNodeId&&findNode?findNode(card.mapNodeId):null;
  return !!(node&&node.planned===false);
}

export function ideaCards(idea){return [...(idea.scenarios||[]),...(idea.pages||[])]}

/* Идея реализована, когда в ней есть карточки и все они реализованы. */
export function isIdeaDone(idea,findNode){
  const cards=ideaCards(idea);
  return cards.length>0&&cards.every(card=>isCardDone(card,findNode));
}

/* Идея попадает в «Backend» (сценарии или пустая) и/или «Frontend» (страницы); реализованные — отдельно, в конце. */
export function groupIdeas(ideas,findNode){
  const groups={backend:[],frontend:[],done:[]};
  for(const idea of ideas){
    if(isIdeaDone(idea,findNode)){groups.done.push(idea);continue}
    const hasPages=(idea.pages||[]).length>0,hasScenarios=(idea.scenarios||[]).length>0;
    if(hasScenarios||!hasPages)groups.backend.push(idea);
    if(hasPages)groups.frontend.push(idea);
  }
  return groups;
}

/* Карточку принёс агент: у неё есть решение цепочки или идея создана агентом. */
export function isAgentCard(card,idea){
  return !!(card&&card.decision)||!!(idea&&idea.created_by==="agent");
}

/* Жест над карточкой: вверх — на карту, вниз — удалить (иначе возвращается на место). */
export function swipeAction(verdict){
  return verdict==="accepted"?"to-map":verdict==="rejected"?"delete":null;
}

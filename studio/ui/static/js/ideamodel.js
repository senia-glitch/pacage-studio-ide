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
  return {id:text(raw.id)||id(),source,title:text(raw.title||raw.name)||`Страница ${index+1}`,name:text(raw.name),description:text(raw.description),acceptance:strings(raw.acceptance),domain:text(raw.domain),route:text(raw.route),calls:strings(raw.calls),blocks:(Array.isArray(raw.blocks)?raw.blocks:[]).map(blockFrom),mapPageId:text(raw.mapPageId),inCode:text(raw.inCode),decision:text(raw.decision),implemented:raw.implemented===true};
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

/* Карточка (сценарий или страница) реализована: её пометили при приёмке, студия нашла её в коде
   (inCode при сверке перед переносом) или узел на карте уже не плановый. */
export function isCardDone(card,findNode){
  if(!card)return false;
  if(card.implemented===true||!!card.inCode)return true;
  const node=card.mapNodeId&&findNode?findNode(card.mapNodeId):null;
  return !!(node&&node.planned===false);
}

export function ideaCards(idea){return [...(idea.scenarios||[]),...(idea.pages||[])]}

/* Карточка уже разобрана: перенесена на карту/во фронтенд, есть в коде или реализована.
   Такие карточки уходят из поля зрения — работать остаётся только с неразобранными. */
export function isHandledCard(card,findNode){
  if(!card)return false;
  if(card.planSlug||card.mapPageId||card.inCode||card.implemented===true)return true;
  return !!(card.mapNodeId&&findNode&&findNode(card.mapNodeId));
}

/* Колесо мыши над горизонтальной лентой карточек: на сколько пикселей сдвинуть ленту.
   0 — колесо не трогаем (нет что прокручивать, горизонтальный жест, край ленты, прокручивается текстовое поле). */
export function wheelShift(event,box,innerCanScroll){
  if(!event||!box||event.ctrlKey||innerCanScroll)return 0;
  const dy=Number(event.deltaY)||0,dx=Number(event.deltaX)||0;
  if(!dy||Math.abs(dx)>=Math.abs(dy))return 0;
  const max=box.scrollWidth-box.clientWidth;
  if(max<=0)return 0;
  const unit=event.deltaMode===1?16:event.deltaMode===2?box.clientWidth:1;
  const next=Math.max(0,Math.min(max,box.scrollLeft+dy*unit));
  return next===box.scrollLeft?0:next-box.scrollLeft;
}

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

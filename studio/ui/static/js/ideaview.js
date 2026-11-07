import {api} from "./api.js";
import {S, esc, setTab, schedulePersist, toast, applyGraph, hooks, modal} from "./core.js";
import {storageKey} from "./storage.js";
import {id, text, strings, blockFrom, pageFrom, pagesFrom, pageSource, plural, railCounts, railLabel, groupIdeas, ideaCards, isAgentCard, isCardDone, swipeAction} from "./ideamodel.js";
import {swipeDecision, swipeProgress} from "./roadmapmodel.js";

const EMPTY = {version: 1, activeId: "", ideas: []};
let board = EMPTY;
let persistTimer = null;
let loadedKey = "";
let lifecycleBound = false;
let promptMode = "new";
/* Отмена действий: снапшоты доски перед деструктивными операциями. */
const undoStack = [];
let syncTimer = null;
/* Ревизия серверной доски: по ней сервер отличает «моё устаревшее» от «чужого свежего»
   (агент или другая вкладка правят .studio/ideas.json) и отвечает 409 вместо перезаписи. */
let boardRevision = 0;
let dirty = false;
/* Какая ветка идеи открыта: сценарии бэкенда или страницы фронтенда. */
let branchView = "backend";
let serverSynced = false;
/* Поиск по идеям — свой, в левой панели страницы; свёрнутые группы помним в браузере. */
let ideaQuery = "";
const GROUPS_KEY = "studio:idea-groups-closed";
const closedGroups = new Set((() => { try { return JSON.parse(localStorage.getItem(GROUPS_KEY) || "[]"); } catch (_) { return []; } })());
function rememberGroups() { try { localStorage.setItem(GROUPS_KEY, JSON.stringify([...closedGroups])); } catch (_) { /* без хранилища */ } }

function key(){ return `${storageKey((S.G&&S.G.project_root)||location.origin)}:ideas:v1`; }
function clone(value){return JSON.parse(JSON.stringify(value))}
function promptBasis(idea){return JSON.stringify([idea.title,idea.description])}
function normalizeBoard(value){
  if(!value||!Array.isArray(value.ideas))return clone(EMPTY);
  const ideas=value.ideas.filter(item=>item&&typeof item==="object"&&!Array.isArray(item)).map((item,index)=>{
    const idea={id:text(item.id)||id(),created_by:text(item.created_by),title:text(item.title)||`Идея ${index+1}`,description:text(item.description),prompt:text(item.prompt),promptBasis:text(item.promptBasis),jsonText:text(item.jsonText),payload:item.payload&&typeof item.payload==="object"?item.payload:{scenarios:[]},scenarios:[],pages:pagesFrom(item.pages)};
    idea.scenarios=(Array.isArray(item.scenarios)?item.scenarios:[]).filter(s=>s&&typeof s==="object"&&!Array.isArray(s)).map((scenario,scenarioIndex)=>{
      const source=scenario.source&&typeof scenario.source==="object"&&!Array.isArray(scenario.source)?scenario.source:{};
      const acceptance=Array.isArray(scenario.acceptance)?scenario.acceptance.map(String):[];
      return {id:text(scenario.id)||id(),source,title:text(scenario.title)||scenarioName(source,scenarioIndex),description:text(scenario.description)||text(source.description),acceptance,domain:text(scenario.domain)||text(source.domain),mapNodeId:text(scenario.mapNodeId),mapEndpointId:text(scenario.mapEndpointId),decision:text(scenario.decision),planSlug:text(scenario.planSlug),inCode:text(scenario.inCode),implemented:scenario.implemented===true};
    });
    idea.payload=payloadFor(idea);if(!idea.jsonText&&(idea.scenarios.length||idea.pages.length))idea.jsonText=JSON.stringify(idea.payload,null,2);
    return idea;
  });
  const requested=text(value.activeId);
  return {version:1,activeId:ideas.some(item=>item.id===requested)?requested:(ideas[0]?.id||""),ideas};
}
function pushUndo(){
  undoStack.push(clone(board));
  if(undoStack.length>30)undoStack.shift();
  updateUndoButton();
}
function undoBoard(){
  if(!undoStack.length){toast("Отменять нечего");return}
  board=undoStack.pop();
  render();save();
  updateUndoButton();
  toast("Действие отменено");
}
function updateUndoButton(){
  const button=$("ideasUndoBtn");
  if(button)button.hidden=!undoStack.length;
}
function indicator(state,textValue){if(state)state.textContent=textValue}
function localCopy(){return JSON.stringify({...board,revision:boardRevision})}
function adoptRemote(raw){
  pushUndo();
  board=normalizeBoard(raw);
  boardRevision=Number(raw&&raw.revision)||0;
  dirty=false;
  try{localStorage.setItem(loadedKey,localCopy())}catch(_){ }
  render();
  toast("Доску идей изменил агент или другая вкладка — загружена серверная версия (можно отменить)");
}
async function pushBoard(){
  try{
    const result=await api("/api/ideas","POST",{...board,revision:boardRevision});
    boardRevision=Number(result&&result.revision)||boardRevision;
    dirty=false;
    try{localStorage.setItem(loadedKey,localCopy())}catch(_){ }
  }catch(error){
    if(error&&error.code==="ideas_conflict"&&error.details&&error.details.board){adoptRemote(error.details.board);return}
    throw error;
  }
}
function save(){
  clearTimeout(persistTimer);
  dirty=true;
  const state=$("ideasSaveState"); if(state)state.textContent="Сохранение…";
  persistTimer=setTimeout(()=>{
    try{localStorage.setItem(loadedKey,localCopy())}catch(_){indicator(state,"Не удалось сохранить");return}
    syncToServer(state);
  },220);
}
function syncToServer(state){
  clearTimeout(syncTimer);
  syncTimer=setTimeout(async()=>{
    try{
      await pushBoard();
      serverSynced=true;
      indicator(state,"Все изменения сохранены в проект");
    }catch(_){
      indicator(state,"Сохранено локально — нет связи со студией");
      if(serverSynced){serverSynced=false;syncToServer(state)}
    }
  },350);
}
function flushSave(){
  clearTimeout(persistTimer);
  try{localStorage.setItem(loadedKey,localCopy());const state=$("ideasSaveState");if(state)state.textContent="Все изменения сохранены в проект"}
  catch(_){const state=$("ideasSaveState");if(state)state.textContent="Не удалось сохранить"}
  clearTimeout(syncTimer);
  pushBoard().catch(()=>{ });
}
function load(){
  loadedKey=key();
  boardRevision=0;
  try{
    const stored=JSON.parse(localStorage.getItem(loadedKey)||"null");
    board=normalizeBoard(stored);
    boardRevision=Number(stored&&stored.revision)||0;
  }
  catch(_){board=clone(EMPTY)}
  try{promptMode=localStorage.getItem(loadedKey+":promptMode")==="improve"?"improve":"new"}catch(_){promptMode="new"}
}
/* Серверная доска авторитетна: подгружаем её при первой загрузке вкладки.
   Если в проекте доски ещё нет, а локальная есть — переносим её в .studio. */
async function syncFromServer(){
  const state=$("ideasSaveState");
  try{
    const raw=await api("/api/ideas","GET");
    const remote=normalizeBoard(raw);
    const remoteRevision=Number(raw&&raw.revision)||0;
    if(!board.ideas.length&&remote.ideas.length){
      board=remote;boardRevision=remoteRevision;render();
    }else if(!remote.ideas.length&&board.ideas.length){
      boardRevision=remoteRevision;
      await pushBoard();
    }else if(remoteRevision!==boardRevision&&!dirty){
      board=remote;boardRevision=remoteRevision;render();
    }
    serverSynced=true;
    indicator(state,board.ideas.length?"Все изменения сохранены в проект":"Все изменения сохранены");
  }catch(_){
    serverSynced=false;
    indicator(state,"Сохранено локально — нет связи со студией");
  }
}
function $(id){return document.getElementById(id)}
function scenarioName(item,index){return text(item.title||item.name||item.slug)||`Сценарий ${index+1}`}
function normalizedScenarios(value){
  const list=Array.isArray(value)?value:(value&&Array.isArray(value.scenarios)?value.scenarios:null);
  if(!list)throw new Error('В корне JSON ожидается массив "scenarios".');
  return list.map((source,index)=>{
    if(!source||typeof source!=="object"||Array.isArray(source))throw new Error(`scenarios[${index}] должен быть объектом.`);
    const item={...source};
    return {id:id(),source:item,title:scenarioName(item,index),description:text(item.description),acceptance:Array.isArray(item.acceptance)?item.acceptance.map(String):[],domain:text(item.domain)};
  });
}
function uid(){return (globalThis.crypto&&crypto.randomUUID)?crypto.randomUUID():`${Date.now().toString(16)}-${Math.random().toString(16).slice(2,10)}`}
function syncPage(idea,page){
  page.source=pageSource(page);
  idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);save();
}
function payloadFor(idea){
  const parsed=idea.payload&&typeof idea.payload==="object"?clone(idea.payload):{scenarios:[]};
  if(Array.isArray(parsed))return idea.scenarios.map(s=>({...s.source}));
  parsed.scenarios=idea.scenarios.map(s=>({...s.source}));
  if((idea.pages&&idea.pages.length)||Array.isArray(parsed.pages))parsed.pages=(idea.pages||[]).map(p=>({...p.source}));
  return parsed;
}
function syncScenario(idea,scenario){
  scenario.source={...scenario.source,title:scenario.title,description:scenario.description,acceptance:scenario.acceptance,domain:scenario.domain};
  idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);save();
}
/* ---------- связь сценария с плановыми узлами на карте ---------- */
function domainSet(value){return String(value||"").split(",").map(x=>x.trim()).filter(Boolean).sort().join(",")}
function sameAcceptance(left,right){
  const a=(Array.isArray(left)?left:[]).map(x=>String(x).trim()).filter(Boolean).sort();
  const b=(Array.isArray(right)?right:[]).map(x=>String(x).trim()).filter(Boolean).sort();
  return a.length===b.length&&a.every((item,index)=>item===b[index]);
}
function mapScenarioNode(scenario){
  if(!scenario.mapNodeId||!S.G||!Array.isArray(S.G.nodes))return null;
  return S.G.nodes.find(node=>node.id===scenario.mapNodeId)||null;
}
function isOnMap(scenario){return !!scenario.planSlug||!!mapScenarioNode(scenario)}
function hydrateMapLinks(){
  if(!S.G||!Array.isArray(S.G.nodes))return;
  const nodes=S.G.nodes,edges=Array.isArray(S.G.edges)?S.G.edges:[];
  const ids=new Set(nodes.map(node=>node.id));
  const claimed=new Set();
  let changed=false;
  for(const idea of board.ideas)for(const scenario of idea.scenarios)
    if(scenario.mapNodeId&&ids.has(scenario.mapNodeId))claimed.add(scenario.mapNodeId);
  for(const idea of board.ideas)for(const scenario of idea.scenarios)
    if(scenario.mapNodeId&&!ids.has(scenario.mapNodeId)){
      scenario.mapNodeId="";changed=true;
      const endpoint=scenario.mapEndpointId?nodes.find(node=>node.id===scenario.mapEndpointId&&node.kind==="endpoint"&&node.planned&&String(node.id||"").startsWith("studio:")):null;
      if(!endpoint)scenario.mapEndpointId="";
    }
  for(const idea of board.ideas)for(const scenario of idea.scenarios){
    if(scenario.mapNodeId)continue;
    const match=nodes.find(node=>node.kind==="scenario"&&node.planned&&String(node.id||"").startsWith("studio:")&&!claimed.has(node.id)
      &&node.label===scenario.title&&node.description===scenario.description
      &&domainSet(node.domain)===domainSet(scenario.domain)&&sameAcceptance(node.acceptance,scenario.acceptance));
    if(!match)continue;
    scenario.mapNodeId=match.id;claimed.add(match.id);
    const edge=edges.find(item=>item.to_node===match.id&&["dispatch","scenario_call"].includes(item.kind)&&ids.has(item.from_node)&&String(item.from_node).startsWith("studio:")&&nodes.some(node=>node.id===item.from_node&&node.kind==="endpoint"));
    if(edge)scenario.mapEndpointId=edge.from_node;
    changed=true;
  }
  if(changed)save();
}
function addIdea(){
  const search=$("ideaSearch");if(search&&search.value){search.value="";ideaQuery="";const count=$("ideaSearchCount");if(count)count.textContent=""}
  const idea={id:id(),title:"Новая идея",description:"",prompt:"",jsonText:"",payload:{scenarios:[]},scenarios:[],pages:[]};
  board.ideas.push(idea);board.activeId=idea.id;render();save();
  const input=document.querySelector(`[data-idea-title="${idea.id}"]`);if(input){input.focus();input.select()}
}
function pageCard(page,index,idea){
  const sent=!!page.mapPageId;
  const blocks=page.blocks.map(b=>`<div class="idea-block-row" data-block="${esc(b.id)}">
      <div class="idea-scenario-head"><input class="scenario-title" data-bfield="name" aria-label="Название блока" value="${esc(b.name)}"><button class="icon-button danger" type="button" data-delete-block="${esc(b.id)}" title="Удалить блок" aria-label="Удалить блок">×</button></div>
      <textarea data-bfield="description" rows="2" placeholder="Что делает блок">${esc(b.description)}</textarea>
      <textarea data-bfield="acceptance" rows="2" placeholder="Критерии: по одному на строку">${esc(b.acceptance.join("\n"))}</textarea>
      <textarea data-bfield="calls" rows="1" placeholder="Вызывает: GET /path (по одному на строку)">${esc(b.calls.join("\n"))}</textarea>
    </div>`).join("");
  return `<article class="idea-scenario-card idea-page-card swipeable${sent?" on-map":""}" data-page="${esc(page.id)}">
    <div class="idea-scenario-head"><span class="swipe-grip" title="Потяните карточку: вверх — во фронтенд, вниз — удалить">⇅</span><span class="scenario-index">${String(index+1).padStart(2,"0")}</span>${isAgentCard(page,idea)?'<span class="agent-badge">агент</span>':""}${isCardDone(page)?'<span class="scenario-on-map">реализовано</span>':""}<input class="scenario-title" data-pfield="title" aria-label="Название страницы" value="${esc(page.title)}"><button class="text-button" type="button" data-page-to-front="${esc(page.id)}" title="Создать плановую страницу и блоки во вкладке «Фронтенд»">Во фронтенд ↗</button><button class="icon-button danger" type="button" data-delete-page="${esc(page.id)}" title="Удалить страницу из ветки" aria-label="Удалить страницу">×</button></div>
    ${sent?'<div class="hint">✓ Передана во вкладку «Фронтенд». «Во фронтенд ↗» обновит те же узлы.</div>':""}
    ${page.inCode?`<div class="hint">✓ Уже есть в коде: ${esc(page.inCode)}. Во фронтенд не переносится.</div>`:""}
    <label class="idea-field-label">ПАПКА СТРАНИЦЫ<input data-pfield="name" value="${esc(page.name)}" placeholder="orders-list"></label>
    <label class="idea-field-label">МАРШРУТ<input data-pfield="route" value="${esc(page.route)}" placeholder="/orders"></label>
    <label class="idea-field-label">ОПИСАНИЕ<textarea data-pfield="description" rows="4" placeholder="Что делает страница…">${esc(page.description)}</textarea></label>
    <label class="idea-field-label">КРИТЕРИИ ПРИЁМКИ<textarea data-pfield="acceptance" rows="3" placeholder="По одному критерию на строку">${esc(page.acceptance.join("\n"))}</textarea></label>
    <label class="idea-field-label">РОЛЬ (ДОМЕН)<input data-pfield="domain" value="${esc(page.domain)}" placeholder="клиент"></label>
    <label class="idea-field-label">ВЫЗЫВАЕТ ЭНДПОИНТЫ<textarea data-pfield="calls" rows="2" placeholder="GET /orders/list&#10;POST /orders/create">${esc(page.calls.join("\n"))}</textarea></label>
    <div class="idea-page-blocks"><div class="branch-label">БЛОКИ <span>${page.blocks.length}</span></div>${blocks||'<div class="hint">Блоков нет</div>'}</div>
  </article>`;
}
function scenarioBranch(idea,children){
  return `<div class="idea-branch"><div class="branch-label">СЦЕНАРИИ БЭКЕНДА <span>${idea.scenarios.length}</span></div>${idea.scenarios.length?'<p class="swipe-hint">Потяните карточку за ⇅ или пустое место: вверх — на карту, вниз — удалить</p>':""}<div class="scenario-branch">${children||'<div class="branch-empty"><span>＋</span><b>Ветка пока пустая</b><small>Вставьте JSON от LLM и разберите ответ</small></div>'}</div></div>`;
}
function pageBranch(idea){
  return `<div class="idea-branch"><div class="branch-label">СТРАНИЦЫ ФРОНТЕНДА <span>${idea.pages.length}</span></div>${idea.pages.length?'<p class="swipe-hint">Потяните карточку: вверх — во фронтенд, вниз — удалить</p>':""}<div class="scenario-branch">${idea.pages.map((page,index)=>pageCard(page,index,idea)).join("")}</div></div>`;
}
function branchTabs(idea,view){
  if(!idea.pages.length)return "";
  return `<div class="idea-branch-tabs" role="tablist"><button type="button" role="tab" class="${view==="backend"?"on":""}" data-branch-tab="backend">Бэкенд <span>${idea.scenarios.length}</span></button><button type="button" role="tab" class="${view==="frontend"?"on":""}" data-branch-tab="frontend">Фронтенд <span>${idea.pages.length}</span></button></div>`;
}
function card(idea){
  const view=idea.pages.length&&(branchView==="frontend"||!idea.scenarios.length)?"frontend":"backend";
  const promptIsFresh=!!idea.prompt&&idea.promptBasis===promptBasis(idea);
  const children=idea.scenarios.map((s,index)=>{
    const onMap=isOnMap(s);
    return `<article class="idea-scenario-card swipeable${onMap||s.inCode?" on-map":""}" data-scenario="${esc(s.id)}">
    <div class="idea-scenario-head"><span class="swipe-grip" title="Потяните карточку: вверх — на карту, вниз — удалить">⇅</span><span class="scenario-index">${String(index+1).padStart(2,"0")}</span>${isAgentCard(s,idea)?'<span class="agent-badge">агент</span>':""}${isCardDone(s,id=>S.byId&&S.byId[id])?'<span class="scenario-on-map">реализовано</span>':""}${onMap?'<span class="scenario-on-map" title="Плановые узлы сценария уже созданы на карте">на карте</span>':""}${s.inCode?`<span class="scenario-on-map" title="${esc(s.inCode)}">уже в коде</span>`:""}<input class="scenario-title" data-field="title" aria-label="Название сценария" value="${esc(s.title)}"><button class="text-button" type="button" data-to-plan="${esc(s.id)}" title="${onMap?"Уже на карте — подтвердите пересоздание узлов":"Создать плановые узлы на общей карте"}">На карту ↗</button><button class="icon-button danger" type="button" data-delete-scenario="${esc(s.id)}" title="Удалить ветку сценария" aria-label="Удалить сценарий">×</button></div>
    <label class="idea-field-label">ОПИСАНИЕ<textarea data-field="description" rows="5" placeholder="Опишите сценарий…">${esc(s.description)}</textarea></label>
    <label class="idea-field-label">КРИТЕРИИ ПРИЁМКИ<textarea data-field="acceptance" rows="3" placeholder="По одному критерию на строку">${esc(s.acceptance.join("\n"))}</textarea></label>
    <label class="idea-field-label">ДОМЕН<input data-field="domain" value="${esc(s.domain)}" placeholder="orders"></label>
  </article>`;
  }).join("");
  return `<section class="idea-row" data-idea="${esc(idea.id)}">
    <article class="idea-root-card">
      <div class="node-kicker"><span class="node-dot"></span> ИДЕЯ <span class="node-state">Корень ветки${idea.created_by==="agent"?" · от агента":""}</span><button type="button" class="icon-button danger" data-delete-idea="${esc(idea.id)}" title="Удалить идею">×</button></div>
      <input class="idea-title" data-idea-title="${esc(idea.id)}" value="${esc(idea.title)}" aria-label="Название идеи">
      <label class="idea-field-label">ЗАДАЧА / ИДЕЯ<textarea data-idea-description="${esc(idea.id)}" rows="4" placeholder="Какую потребность или проблему нужно решить?">${esc(idea.description)}</textarea></label>
      <div class="idea-node-actions manual-only"><select class="prompt-mode" data-prompt-mode="${esc(idea.id)}" title="Режим сборки промпта"><option value="new"${promptMode==="new"?" selected":""}>Новая система</option><option value="improve"${promptMode==="improve"?" selected":""}>Развитие системы</option></select><button type="button" class="primary" data-generate-prompt="${esc(idea.id)}">✦ Собрать промпт</button><button type="button" data-copy-prompt="${esc(idea.id)}" ${promptIsFresh?"":"disabled"}>Копировать</button><span class="prompt-freshness ${promptIsFresh?"fresh":idea.prompt?"stale":""}" data-prompt-state="${esc(idea.id)}">${promptIsFresh?"Актуален":idea.prompt?"Устарел":""}</span></div>
      <details class="idea-prompt-details manual-only"><summary>Промпт для LLM</summary><textarea data-idea-prompt="${esc(idea.id)}" rows="7" readonly placeholder="Соберите промпт на основе идеи">${esc(idea.prompt)}</textarea></details>
      <div class="idea-json-head manual-only"><label class="idea-field-label">ОТВЕТ LLM · JSON</label><button type="button" class="text-button" data-import-json="${esc(idea.id)}">Разобрать ответ ↓</button></div>
      <textarea class="idea-json manual-only" data-idea-json="${esc(idea.id)}" rows="7" placeholder='Вставьте ответ LLM: JSON с "scenarios" и, если нужен интерфейс, "pages"'>${esc(idea.jsonText)}</textarea>
      <div class="idea-json-foot manual-only">${idea.jsonText&&idea.jsonText!==JSON.stringify(idea.payload,null,2)?'JSON изменён — нажмите «Разобрать ответ»':(idea.scenarios.length||idea.pages.length)?`${railCounts(idea)} в ветке`:'Ответ ещё не разобран'}</div>
    </article>
    <div class="idea-branches">${branchTabs(idea,view)}${view==="frontend"?pageBranch(idea):scenarioBranch(idea,children)}</div>
  </section>`;
}
function render(){
  hydrateMapLinks();
  const boardEl=$("ideasBoard"),list=$("ideaList");if(!boardEl||!list)return;
  const query=String(ideaQuery||"").trim().toLocaleLowerCase("ru");
  const visible=board.ideas.filter(idea=>!query||[idea.title,idea.description,...idea.scenarios.flatMap(s=>[s.title,s.description,s.domain]),...idea.pages.flatMap(p=>[p.title,p.name,p.description,p.domain])].some(value=>String(value||"").toLocaleLowerCase("ru").includes(query)));
  const count=$("ideaSearchCount");if(count)count.textContent=query?`идей: ${visible.length} из ${board.ideas.length}`:"";
  if(visible.length&&!visible.some(idea=>idea.id===board.activeId))board.activeId=visible[0].id;
  const active=visible.find(idea=>idea.id===board.activeId);
  boardEl.innerHTML=active?card(active):query?'<div class="ideas-welcome"><span>⌕</span><h3>Ничего не найдено</h3><p>Попробуйте изменить поисковый запрос.</p></div>':board.ideas.length?'<div class="ideas-welcome"><span>⌁</span><h3>Выберите идею</h3><p>Выберите ветку в списке слева.</p></div>':'<div class="ideas-welcome"><span>✳</span><h3>Начните с идеи</h3><p>Создайте корневой узел, сформируйте промпт для LLM и добавьте сценарии в виде связанной ветки.</p><button class="primary" type="button" data-add-first>＋ Создать первую идею</button></div>';
  const findNode=nodeId=>S.byId&&S.byId[nodeId];
  const railItem=idea=>`<button type="button" class="idea-rail-item${idea.id===board.activeId?" on":""}" data-focus-idea="${esc(idea.id)}"><span>${String(visible.indexOf(idea)+1).padStart(2,"0")}</span><b>${esc(idea.title||"Без названия")}</b><small>${railLabel(idea)}</small></button>`;
  const railGroup=(key,title,items)=>items.length?`<div class="rail-group${closedGroups.has(key)?" closed":""}"><button type="button" class="rail-group-head" data-toggle-group="${key}" aria-expanded="${closedGroups.has(key)?"false":"true"}">${closedGroups.has(key)?"▸":"▾"} ${title}<span>${items.length}</span></button><div class="rail-group-body">${items.map(railItem).join("")}</div></div>`:"";
  const grouped=groupIdeas(visible,findNode);
  list.innerHTML=visible.length?railGroup("backend","Backend",grouped.backend)+railGroup("frontend","Frontend",grouped.frontend)+railGroup("done","Реализованные",grouped.done):query?'<p class="rail-empty">Совпадений нет</p>':'<p class="rail-empty">Пока нет идей</p>';
  list.querySelectorAll("[data-toggle-group]").forEach(el=>el.onclick=()=>{const key=el.dataset.toggleGroup;if(closedGroups.has(key))closedGroups.delete(key);else closedGroups.add(key);rememberGroups();render()});
  boardEl.querySelectorAll("[data-idea-title]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaTitle);if(!idea)return;idea.title=el.value;updateRail();updatePromptState(idea);save()});
  boardEl.querySelectorAll("[data-idea-description]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaDescription);if(idea){idea.description=el.value;updatePromptState(idea);save()}});
  boardEl.querySelectorAll("[data-idea-json]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaJson);if(idea)idea.jsonText=el.value;save()});
  boardEl.querySelectorAll("[data-field]").forEach(el=>el.oninput=()=>{
    const card=el.closest("[data-scenario]");const idea=find(el.closest("[data-idea]").dataset.idea);const scenario=idea&&idea.scenarios.find(x=>x.id===card.dataset.scenario);if(!scenario)return;
    if(el.dataset.field==="acceptance")scenario.acceptance=el.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);else scenario[el.dataset.field]=el.value;
    syncScenario(idea,scenario);
    const json=boardEl.querySelector(`[data-idea-json="${idea.id}"]`);if(json)json.value=idea.jsonText;
  });
  const pageOf=el=>{const idea=find(el.closest("[data-idea]").dataset.idea);return {idea,page:idea&&idea.pages.find(x=>x.id===el.closest("[data-page]").dataset.page)}};
  boardEl.querySelectorAll("[data-pfield]").forEach(el=>el.oninput=()=>{
    const {idea,page}=pageOf(el);if(!page)return;
    const key=el.dataset.pfield;
    page[key]=(key==="acceptance"||key==="calls")?strings(el.value.split(/\r?\n/)):el.value;
    syncPage(idea,page);
    const json=boardEl.querySelector(`[data-idea-json="${idea.id}"]`);if(json)json.value=idea.jsonText;
  });
  boardEl.querySelectorAll("[data-bfield]").forEach(el=>el.oninput=()=>{
    const {idea,page}=pageOf(el);if(!page)return;
    const block=page.blocks.find(x=>x.id===el.closest("[data-block]").dataset.block);if(!block)return;
    const key=el.dataset.bfield;
    block[key]=(key==="acceptance"||key==="calls")?strings(el.value.split(/\r?\n/)):el.value;
    syncPage(idea,page);
    const json=boardEl.querySelector(`[data-idea-json="${idea.id}"]`);if(json)json.value=idea.jsonText;
  });
  boardEl.querySelectorAll("[data-delete-page]").forEach(el=>el.onclick=()=>{
    const idea=find(el.closest("[data-idea]").dataset.idea);if(!idea)return;
    pushUndo();idea.pages=idea.pages.filter(x=>x.id!==el.dataset.deletePage);
    idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);render();save();toast("Страница удалена из ветки и JSON");
  });
  boardEl.querySelectorAll("[data-delete-block]").forEach(el=>el.onclick=()=>{
    const {idea,page}=pageOf(el);if(!page)return;
    pushUndo();page.blocks=page.blocks.filter(x=>x.id!==el.dataset.deleteBlock);
    syncPage(idea,page);render();toast("Блок удалён");
  });
  boardEl.querySelectorAll("[data-page-to-front]").forEach(el=>el.onclick=()=>{
    const {page}=pageOf(el);if(page)sendPageToFront(page,el);
  });
  boardEl.querySelectorAll("[data-branch-tab]").forEach(el=>el.onclick=()=>{branchView=el.dataset.branchTab;render()});
  boardEl.querySelectorAll("[data-delete-idea]").forEach(el=>el.onclick=()=>{if(!confirm("Удалить идею вместе со всеми сценариями?"))return;pushUndo();board.ideas=board.ideas.filter(x=>x.id!==el.dataset.deleteIdea);if(board.activeId===el.dataset.deleteIdea)board.activeId=board.ideas[0]?.id||"";render();save()});
  boardEl.querySelectorAll("[data-delete-scenario]").forEach(el=>el.onclick=()=>{const idea=find(el.closest("[data-idea]").dataset.idea);if(!idea)return;pushUndo();idea.scenarios=idea.scenarios.filter(x=>x.id!==el.dataset.deleteScenario);idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);render();save();toast("Сценарий удалён из ветки и JSON")});
  boardEl.querySelectorAll("[data-to-plan]").forEach(el=>el.onclick=()=>sendToConstructor(el));
  boardEl.querySelectorAll("[data-prompt-mode]").forEach(el=>el.onchange=()=>{promptMode=el.value;try{localStorage.setItem(loadedKey+":promptMode",promptMode)}catch(_){}});
  boardEl.querySelectorAll("[data-generate-prompt]").forEach(el=>el.onclick=()=>generatePrompt(el.dataset.generatePrompt,el));
  boardEl.querySelectorAll("[data-copy-prompt]").forEach(el=>el.onclick=()=>copy(find(el.dataset.copyPrompt)?.prompt||""));
  boardEl.querySelectorAll("[data-import-json]").forEach(el=>el.onclick=()=>importJson(el.dataset.importJson));
  boardEl.querySelectorAll("[data-add-first]").forEach(el=>el.onclick=addIdea);
  list.querySelectorAll("[data-focus-idea]").forEach(el=>el.onclick=()=>{board.activeId=el.dataset.focusIdea;render();save()});
  bindSwipe(boardEl);
}
function find(ideaId){return board.ideas.find(x=>x.id===ideaId)}
/* ---------- свайп карточек: вверх — на карту, вниз — удалить ---------- */
function bindSwipe(boardEl){
  boardEl.querySelectorAll(".swipeable").forEach(card=>{
    card.addEventListener("pointerdown",ev=>{
      if(ev.button!==0||ev.target.closest("input,textarea,select,button,a,summary,label"))return;
      const startY=ev.clientY,startAt=performance.now();let dy=0;
      try{card.setPointerCapture(ev.pointerId)}catch(_){ }
      card.classList.add("swiping");
      const move=e=>{
        dy=e.clientY-startY;
        card.style.transform=`translateY(${dy}px) rotate(${Math.max(-2,Math.min(2,dy/60))}deg)`;
        const progress=swipeProgress(dy);
        card.classList.toggle("swipe-up",progress.side==="accepted"&&progress.strength>=.5);
        card.classList.toggle("swipe-down",progress.side==="rejected"&&progress.strength>=.5);
      };
      const finish=e=>{
        card.removeEventListener("pointermove",move);card.removeEventListener("pointerup",finish);card.removeEventListener("pointercancel",finish);
        try{card.releasePointerCapture(e.pointerId)}catch(_){ }
        const speed=Math.abs(dy)/Math.max(1,performance.now()-startAt);
        const action=e.type==="pointercancel"?null:swipeAction(swipeDecision(dy,speed));
        card.classList.remove("swiping","swipe-up","swipe-down");card.style.transform="";
        if(action)void applySwipe(card,action);
      };
      card.addEventListener("pointermove",move);card.addEventListener("pointerup",finish);card.addEventListener("pointercancel",finish);
    });
  });
}
function acceptDecision(item){if(item&&item.decision)item.decision="accepted"}
async function applySwipe(card,action){
  const idea=find(card.closest("[data-idea]")?.dataset.idea);if(!idea)return;
  if(card.dataset.scenario){
    const scenario=idea.scenarios.find(x=>x.id===card.dataset.scenario);if(!scenario)return;
    if(action==="delete"){
      pushUndo();idea.scenarios=idea.scenarios.filter(x=>x.id!==scenario.id);idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);
      render();save();toast("Сценарий удалён (вернуть: Ctrl+Z или «Отменить»)");return;
    }
    if(scenario.planSlug||scenario.inCode||isOnMap(scenario)){acceptDecision(scenario);save();toast(scenario.inCode?`Уже есть в коде: ${scenario.inCode}`:"Сценарий уже на карте");render();return}
    await transferCard("scenario",scenario);render();return;
  }
  const page=idea.pages.find(x=>x.id===card.dataset.page);if(!page)return;
  if(action==="delete"){
    pushUndo();idea.pages=idea.pages.filter(x=>x.id!==page.id);idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);
    render();save();toast("Страница удалена (вернуть: Ctrl+Z или «Отменить»)");return;
  }
  const button=card.querySelector("[data-page-to-front]");if(!button)return;
  await sendPageToFront(page,button,{stay:true});render();
}
/* Перенос карточки делает сервер: он сверяет её с кодом и создаёт план (имена по slug) или плановую страницу.
   Уже реализованное в коде не переносится, карточка получает пометку «уже в коде». */
async function reloadBoard(){
  const raw=await api("/api/ideas","GET");
  board=normalizeBoard(raw);boardRevision=Number(raw&&raw.revision)||0;dirty=false;
  try{localStorage.setItem(loadedKey,localCopy())}catch(_){ }
}
async function transferCard(kind,card,options={}){
  clearTimeout(persistTimer);clearTimeout(syncTimer);
  try{
    await pushBoard();
    const result=await api("/api/workflow/transfer-card","POST",{id:card.id,force:options.force===true});
    await reloadBoard();
    if(result.graph)applyGraph(result.graph);
    if(result.already){toast(`Уже перенесено: ${result.already}`);return result}
    if(!result.transferred){toast(`Уже есть в коде: ${(result.verdict&&result.verdict.where)||"такой узел"}. На карту не переносится`);return result}
    const missing=result.links_missing||[];
    if(kind==="scenario"){
      hooks.renderMap();hooks.renderInspector();hooks.renderIdeas();
      toast(`Сценарий перенесён на карту как план «${result.mark.planSlug}»`);
    }else{
      const partial=result.verdict&&result.verdict.status==="partial"?" (только недостающие блоки)":"";
      toast(missing.length
        ?`Страница добавлена во «Фронтенд»${partial}. Эндпоинтов нет в бэкенде: ${missing.length} (${missing.slice(0,2).join(", ")}${missing.length>2?"…":""}) — передайте сценарии «На карту ↗»`
        :`Страница и блоки добавлены во вкладку «Фронтенд» как плановые узлы${partial}`);
    }
    return result;
  }catch(error){toast("Не удалось перенести карточку: "+error.message);return null}
}
/* Приёмка работы: всё, что стало картой или страницей, считается реализованным и уходит в нижнюю группу. */
export async function markImplemented(){
  await syncFromServer();
  let changed=false;
  for(const idea of board.ideas)for(const card of ideaCards(idea))
    if((card.planSlug||card.mapNodeId||card.mapPageId)&&!card.implemented){card.implemented=true;changed=true}
  if(changed){render();save()}
}
function updatePromptState(idea){
  const fresh=!!idea.prompt&&idea.promptBasis===promptBasis(idea);
  const copyButton=document.querySelector(`[data-copy-prompt="${idea.id}"]`);if(copyButton)copyButton.disabled=!fresh;
  const state=document.querySelector(`[data-prompt-state="${idea.id}"]`);if(state){state.textContent=fresh?"Актуален":idea.prompt?"Устарел":"";state.classList.toggle("fresh",fresh);state.classList.toggle("stale",!!idea.prompt&&!fresh)}
}
async function sendToConstructor(button){
  const idea=find(button.closest("[data-idea]").dataset.idea);const scenario=idea&&idea.scenarios.find(item=>item.id===button.dataset.toPlan);if(!scenario)return;
  if(scenario.planSlug){toast(`Сценарий уже на карте как план «${scenario.planSlug}»`);return}
  if(isOnMap(scenario)){confirmRecreate(scenario);return}
  if(scenario.inCode){
    if(!confirm(`Это уже есть в коде: ${scenario.inCode}.\nПеренести на карту всё равно?`))return;
    const forced=await transferCard("scenario",scenario,{force:true});
    if(forced&&forced.transferred)await setTab("map");
    return;
  }
  const result=await transferCard("scenario",scenario);
  if(result&&result.transferred)await setTab("map");
}
async function sendPageToFront(page,button,options={}){
  if(!page.mapPageId){
    button.disabled=true;
    if(page.inCode&&!confirm(`Это уже есть в коде: ${page.inCode}.\nПеренести во фронтенд всё равно?`)){button.disabled=false;return}
    const result=await transferCard("page",page,{force:!!page.inCode});
    button.disabled=false;
    if(result&&result.transferred&&!options.stay)await setTab("frontend");
    return;
  }
  button.disabled=true;
  try{
    const pageId=page.mapPageId||`page-plan:${uid()}`;
    let last=await api("/api/frontend/meta","POST",{action:"save_page",id:pageId,plannedNew:true,name:page.name||page.title,description:page.description,acceptance:page.acceptance,domain:page.domain,route:page.route,calls:page.calls});
    page.mapPageId=pageId;
    for(const block of page.blocks){
      const blockId=block.mapBlockId||`block-plan:${uid()}`;
      last=await api("/api/frontend/meta","POST",{action:"save_block",pageId,id:blockId,name:block.name,description:block.description,acceptance:block.acceptance,calls:block.calls});
      block.mapBlockId=blockId;
    }
    const mine=new Set([pageId,...page.blocks.map(b=>b.mapBlockId)]);
    const missing=new Set(((last&&last.graph&&last.graph.links&&last.graph.links.rows)||[]).filter(r=>mine.has(r.node)&&r.status!=="ok").map(r=>r.call));
    const idea=find(button.closest("[data-idea]").dataset.idea);
    if(idea)syncPage(idea,page);
    render();
    if(!options.stay)await setTab("frontend");
    toast(missing.size
      ?`Страница добавлена во «Фронтенд». Эндпоинтов нет в бэкенде: ${missing.size} (${[...missing].slice(0,2).join(", ")}${missing.size>2?"…":""}) — передайте сценарии «На карту ↗»`
      :"Страница и блоки добавлены во вкладку «Фронтенд» как плановые узлы");
  }catch(error){button.disabled=false;toast("Не удалось передать страницу во фронтенд: "+error.message)}
}
function confirmRecreate(scenario){
  modal("Сценарий уже есть на карте",
    `<p class="hint">Сценарий «${esc(scenario.title)}» уже добавлен на карту как плановые узлы. Пересоздать?</p>
     <p class="hint">Узлы, созданные из этой карточки, будут обновлены данными карточки. Добавленные вручную потомки и связи сохранятся.</p>`,
    [
      {label:"Нет",action:()=>{}},
      {label:"Да, пересоздать",cls:"primary",action:()=>recreateScenario(scenario)},
    ]);
}
function endpointPayload(scenario,nodeId){
  const payload={kind:"endpoint",name:scenario.title,slug:scenario.source.slug||"",description:scenario.description,acceptance:scenario.acceptance,domain:scenario.domain,http_method:scenario.source.http_method||"GET",path:scenario.source.path||""};
  if(nodeId)payload.node_id=nodeId;
  return payload;
}
async function recreateScenario(scenario){
  try{
    let parentId="";
    if(String(scenario.source.path||"")){
      const reuse=scenario.mapEndpointId&&S.byId[scenario.mapEndpointId]?scenario.mapEndpointId:"";
      const endpoint=await api("/api/metadata/graph-node","POST",endpointPayload(scenario,reuse));
      applyGraph(endpoint.graph);parentId=endpoint.node_id;
    }
    scenario.mapEndpointId=parentId;
    const existing=scenario.mapNodeId&&S.byId[scenario.mapNodeId]?scenario.mapNodeId:"";
    let nodeId="";
    if(existing){
      const updated=await api("/api/metadata/graph-node","POST",{node_id:existing,kind:"scenario",name:scenario.title,slug:scenario.source.slug||"",description:scenario.description,acceptance:scenario.acceptance,domain:scenario.domain});
      applyGraph(updated.graph);
      nodeId=updated.node_id||existing;
    }else{
      const node=await api("/api/metadata/graph-node","POST",{kind:"scenario",name:scenario.title,slug:scenario.source.slug||"",description:scenario.description,acceptance:scenario.acceptance,domain:scenario.domain,parent_id:parentId,edge_kind:"dispatch"});
      applyGraph(node.graph);
      nodeId=node.node_id;
    }
    scenario.mapNodeId=nodeId;
    S.selectedNode=nodeId;hooks.renderMap();hooks.renderInspector();schedulePersist();save();hooks.renderIdeas();
    toast("Сценарий на карте пересоздан данными карточки");
  }catch(error){toast("Не удалось пересоздать сценарий на карте: "+error.message)}
}
function updateRail(){const list=$("ideaList");if(!list)return;list.querySelectorAll(".idea-rail-item").forEach(el=>{const item=find(el.dataset.focusIdea);if(item){el.querySelector("b").textContent=item.title||"Без названия";el.querySelector("small").textContent=railLabel(item)}})}
async function generatePrompt(ideaId,button){
  const idea=find(ideaId);if(!idea)return;if(!idea.description.trim()){toast("Сначала опишите идею");return}
  button.disabled=true;button.textContent="Собираю…";
  try{const data=await api("/api/decompose/prompt","POST",{task:`${idea.title}\n\n${idea.description}`,mode:promptMode});idea.prompt=data.prompt||"";idea.promptBasis=promptBasis(idea);render();save();toast("Промпт готов")}
  catch(error){toast("Не удалось собрать промпт: "+error.message);button.disabled=false;button.textContent="✦ Собрать промпт"}
}
async function parseAnswer(textValue){
  try{return await api("/api/decompose/parse","POST",{text:textValue})}
  catch(error){
    if(error&&error.status)throw error;
    const parsed=JSON.parse(textValue);
    return Array.isArray(parsed)?{scenarios:parsed,pages:[],warnings:[]}:{scenarios:parsed.scenarios||[],pages:parsed.pages||[],warnings:[]};
  }
}
async function importJson(ideaId){
  const idea=find(ideaId);if(!idea)return;
  try{
    const parsed=await parseAnswer(idea.jsonText);
    const scenarios=normalizedScenarios(parsed.scenarios||[]);
    const pages=pagesFrom(parsed.pages);
    if((idea.scenarios.length||idea.pages.length)&&!confirm("Заменить ветку этой идеи? Текущие правки карточек будут перезаписаны."))return;
    pushUndo();
    idea.payload={scenarios:parsed.scenarios||[],...(parsed.pages&&parsed.pages.length?{pages:parsed.pages}:{})};
    idea.scenarios=scenarios;idea.pages=pages;
    idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);
    render();save();
    toast(`Добавлено: сценариев ${scenarios.length}, страниц ${pages.length}`);
    const warnings=Array.isArray(parsed.warnings)?parsed.warnings:[];
    if(warnings.length)toast(warnings.slice(0,2).join("; "));
  }catch(error){toast("JSON не разобран: "+error.message)}
}
function copy(value){if(!value){toast("Сначала соберите промпт");return}if(navigator.clipboard?.writeText)navigator.clipboard.writeText(value).then(()=>toast("Промпт скопирован")).catch(()=>toast("Не удалось скопировать"));else{const el=document.createElement("textarea");el.value=value;document.body.appendChild(el);el.select();document.execCommand("copy");el.remove();toast("Промпт скопирован")}}
function bind(){
  $("addIdeaBtn")?.addEventListener("click",addIdea);$("addIdeaEmpty")?.addEventListener("click",addIdea);
  $("ideasExportBtn")?.addEventListener("click",exportBoard);
  $("ideasImportBtn")?.addEventListener("click",()=>$("ideasImportFile")?.click());
  $("ideasImportFile")?.addEventListener("change",importBoard);
  $("ideaSearch")?.addEventListener("input",ev=>{ideaQuery=ev.target.value.trim();render()});
}
function exportBoard(){
  const blob=new Blob([JSON.stringify(board,null,2)],{type:"application/json;charset=utf-8"});
  const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download="studio-ideas.json";document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),1000);toast("Доска идей скачана")
}
async function importBoard(event){
  const input=event.currentTarget,file=input.files&&input.files[0];if(!file)return;
  try{const incoming=normalizeBoard(JSON.parse(await file.text()));if(board.ideas.length&&!confirm("Заменить текущую доску идеями из файла? Текущие идеи будут перезаписаны."))return;pushUndo();board=incoming;render();save();toast(`Загружено идей: ${board.ideas.length}`)}
  catch(error){toast("Не удалось загрузить доску: "+error.message)}
  finally{input.value=""}
}
export function refreshIdeas(){if(loadedKey===key())syncFromServer()}
export function renderIdeas(){if(loadedKey!==key()){load();serverSynced=false}render();syncFromServer()}
export function initIdeas(){
  load();bind();render();
  $("ideasUndoBtn")?.addEventListener("click",undoBoard);
  document.addEventListener("keydown",ev=>{
    if(S.tab!=="ideas"||!undoStack.length)return;
    const tag=(document.activeElement?.tagName||"").toLowerCase();
    if(tag==="input"||tag==="textarea"||document.activeElement?.isContentEditable)return;
    if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==="z"&&!ev.shiftKey){ev.preventDefault();undoBoard()}
  });
  if(!lifecycleBound){window.addEventListener("pagehide",flushSave);lifecycleBound=true}
}

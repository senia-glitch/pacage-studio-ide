import {api} from "./api.js";
import {S, esc, setTab, schedulePersist, toast} from "./core.js";
import {normalizeDraft} from "./chainmodel.js";
import {storageKey} from "./storage.js";

const EMPTY = {version: 1, activeId: "", ideas: []};
let board = EMPTY;
let persistTimer = null;
let loadedKey = "";
let lifecycleBound = false;

function key(){ return `${storageKey((S.G&&S.G.project_root)||location.origin)}:ideas:v1`; }
function clone(value){return JSON.parse(JSON.stringify(value))}
function promptBasis(idea){return JSON.stringify([idea.title,idea.description])}
function normalizeBoard(value){
  if(!value||!Array.isArray(value.ideas))return clone(EMPTY);
  const ideas=value.ideas.filter(item=>item&&typeof item==="object"&&!Array.isArray(item)).map((item,index)=>{
    const idea={id:text(item.id)||id(),title:text(item.title)||`Идея ${index+1}`,description:text(item.description),prompt:text(item.prompt),promptBasis:text(item.promptBasis),jsonText:text(item.jsonText),payload:item.payload&&typeof item.payload==="object"?item.payload:{scenarios:[]},scenarios:[]};
    idea.scenarios=(Array.isArray(item.scenarios)?item.scenarios:[]).filter(s=>s&&typeof s==="object"&&!Array.isArray(s)).map((scenario,scenarioIndex)=>{
      const source=scenario.source&&typeof scenario.source==="object"&&!Array.isArray(scenario.source)?scenario.source:{};
      const acceptance=Array.isArray(scenario.acceptance)?scenario.acceptance.map(String):[];
      return {id:text(scenario.id)||id(),source,title:text(scenario.title)||scenarioName(source,scenarioIndex),description:text(scenario.description)||text(source.description),acceptance,domain:text(scenario.domain)||text(source.domain)};
    });
    idea.payload=payloadFor(idea);if(!idea.jsonText&&idea.scenarios.length)idea.jsonText=JSON.stringify(idea.payload,null,2);
    return idea;
  });
  const requested=text(value.activeId);
  return {version:1,activeId:ideas.some(item=>item.id===requested)?requested:(ideas[0]?.id||""),ideas};
}
function save(){
  clearTimeout(persistTimer);
  const state=$("ideasSaveState"); if(state)state.textContent="Сохранение…";
  persistTimer=setTimeout(()=>{
    try{localStorage.setItem(loadedKey,JSON.stringify(board));if(state)state.textContent="Все изменения сохранены"}
    catch(_){if(state)state.textContent="Не удалось сохранить"}
  },220);
}
function flushSave(){
  clearTimeout(persistTimer);
  try{localStorage.setItem(loadedKey,JSON.stringify(board));const state=$("ideasSaveState");if(state)state.textContent="Все изменения сохранены"}
  catch(_){const state=$("ideasSaveState");if(state)state.textContent="Не удалось сохранить"}
}
function load(){
  loadedKey=key();
  try{board=normalizeBoard(JSON.parse(localStorage.getItem(loadedKey)||"null"))}
  catch(_){board=clone(EMPTY)}
}
function $(id){return document.getElementById(id)}
function id(){return `idea-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`}
function text(v){return typeof v==="string"?v:""}
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
function payloadFor(idea){
  const parsed=idea.payload&&typeof idea.payload==="object"?clone(idea.payload):{scenarios:[]};
  if(Array.isArray(parsed))return idea.scenarios.map(s=>({...s.source}));
  parsed.scenarios=idea.scenarios.map(s=>({...s.source}));
  return parsed;
}
function syncScenario(idea,scenario){
  scenario.source={...scenario.source,title:scenario.title,description:scenario.description,acceptance:scenario.acceptance,domain:scenario.domain};
  idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);save();
}
function addIdea(){
  const search=$("searchBox");if(search&&search.value){search.value="";S.lastSearchQuery="";const count=$("searchCount");if(count)count.textContent=""}
  const idea={id:id(),title:"Новая идея",description:"",prompt:"",jsonText:"",payload:{scenarios:[]},scenarios:[]};
  board.ideas.push(idea);board.activeId=idea.id;render();save();
  const input=document.querySelector(`[data-idea-title="${idea.id}"]`);if(input){input.focus();input.select()}
}
function card(idea){
  const promptIsFresh=!!idea.prompt&&idea.promptBasis===promptBasis(idea);
  const children=idea.scenarios.map((s,index)=>`<article class="idea-scenario-card" data-scenario="${esc(s.id)}">
    <div class="idea-scenario-head"><span class="scenario-index">${String(index+1).padStart(2,"0")}</span><input class="scenario-title" data-field="title" aria-label="Название сценария" value="${esc(s.title)}"><button class="text-button" type="button" data-to-plan="${esc(s.id)}" title="Передать сценарий в конструктор">В конструктор ↗</button><button class="icon-button danger" type="button" data-delete-scenario="${esc(s.id)}" title="Удалить ветку сценария" aria-label="Удалить сценарий">×</button></div>
    <label class="idea-field-label">ОПИСАНИЕ<textarea data-field="description" rows="5" placeholder="Опишите сценарий…">${esc(s.description)}</textarea></label>
    <label class="idea-field-label">КРИТЕРИИ ПРИЁМКИ<textarea data-field="acceptance" rows="3" placeholder="По одному критерию на строку">${esc(s.acceptance.join("\n"))}</textarea></label>
    <label class="idea-field-label">ДОМЕН<input data-field="domain" value="${esc(s.domain)}" placeholder="orders"></label>
  </article>`).join("");
  return `<section class="idea-row" data-idea="${esc(idea.id)}">
    <article class="idea-root-card">
      <div class="node-kicker"><span class="node-dot"></span> ИДЕЯ <span class="node-state">Корень ветки</span><button type="button" class="icon-button danger" data-delete-idea="${esc(idea.id)}" title="Удалить идею">×</button></div>
      <input class="idea-title" data-idea-title="${esc(idea.id)}" value="${esc(idea.title)}" aria-label="Название идеи">
      <label class="idea-field-label">ЗАДАЧА / ИДЕЯ<textarea data-idea-description="${esc(idea.id)}" rows="4" placeholder="Какую потребность или проблему нужно решить?">${esc(idea.description)}</textarea></label>
      <div class="idea-node-actions"><button type="button" class="primary" data-generate-prompt="${esc(idea.id)}">✦ Собрать промпт</button><button type="button" data-copy-prompt="${esc(idea.id)}" ${promptIsFresh?"":"disabled"}>Копировать</button><span class="prompt-freshness ${promptIsFresh?"fresh":idea.prompt?"stale":""}" data-prompt-state="${esc(idea.id)}">${promptIsFresh?"Актуален":idea.prompt?"Устарел":""}</span></div>
      <details class="idea-prompt-details"><summary>Промпт для LLM</summary><textarea data-idea-prompt="${esc(idea.id)}" rows="7" readonly placeholder="Соберите промпт на основе идеи">${esc(idea.prompt)}</textarea></details>
      <div class="idea-json-head"><label class="idea-field-label">ОТВЕТ LLM · JSON</label><button type="button" class="text-button" data-import-json="${esc(idea.id)}">Разобрать сценарии ↓</button></div>
      <textarea class="idea-json" data-idea-json="${esc(idea.id)}" rows="7" placeholder='Вставьте JSON с массивом "scenarios"'>${esc(idea.jsonText)}</textarea>
      <div class="idea-json-foot">${idea.jsonText&&idea.jsonText!==JSON.stringify(idea.payload,null,2)?'JSON изменён — нажмите «Разобрать сценарии»':idea.scenarios.length?`${idea.scenarios.length} сценариев в ветке`:'Ответ ещё не разобран'}</div>
    </article>
    <div class="idea-branch"><div class="branch-label">СЦЕНАРИИ <span>${idea.scenarios.length}</span></div><div class="scenario-branch">${children||'<div class="branch-empty"><span>＋</span><b>Ветка пока пустая</b><small>Вставьте JSON от LLM и разберите ответ</small></div>'}</div></div>
  </section>`;
}
function render(){
  const boardEl=$("ideasBoard"),list=$("ideaList");if(!boardEl||!list)return;
  const query=String(S.lastSearchQuery||"").trim().toLocaleLowerCase("ru");
  const visible=board.ideas.filter(idea=>!query||[idea.title,idea.description,...idea.scenarios.flatMap(s=>[s.title,s.description,s.domain])].some(value=>String(value||"").toLocaleLowerCase("ru").includes(query)));
  const count=$("searchCount");if(count&&S.tab==="ideas")count.textContent=query?`идей: ${visible.length} из ${board.ideas.length}`:"";
  if(visible.length&&!visible.some(idea=>idea.id===board.activeId))board.activeId=visible[0].id;
  const active=visible.find(idea=>idea.id===board.activeId);
  boardEl.innerHTML=active?card(active):query?'<div class="ideas-welcome"><span>⌕</span><h3>Ничего не найдено</h3><p>Попробуйте изменить поисковый запрос.</p></div>':board.ideas.length?'<div class="ideas-welcome"><span>⌁</span><h3>Выберите идею</h3><p>Выберите ветку в списке слева.</p></div>':'<div class="ideas-welcome"><span>✳</span><h3>Начните с идеи</h3><p>Создайте корневой узел, сформируйте промпт для LLM и добавьте сценарии в виде связанной ветки.</p><button class="primary" type="button" data-add-first>＋ Создать первую идею</button></div>';
  list.innerHTML=visible.length?visible.map((idea,i)=>`<button type="button" class="idea-rail-item${idea.id===board.activeId?" on":""}" data-focus-idea="${esc(idea.id)}"><span>${String(i+1).padStart(2,"0")}</span><b>${esc(idea.title||"Без названия")}</b><small>${idea.scenarios.length} сценариев</small></button>`).join(""):query?'<p class="rail-empty">Совпадений нет</p>':'<p class="rail-empty">Пока нет идей</p>';
  boardEl.querySelectorAll("[data-idea-title]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaTitle);if(!idea)return;idea.title=el.value;updateRail();updatePromptState(idea);save()});
  boardEl.querySelectorAll("[data-idea-description]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaDescription);if(idea){idea.description=el.value;updatePromptState(idea);save()}});
  boardEl.querySelectorAll("[data-idea-json]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaJson);if(idea)idea.jsonText=el.value;save()});
  boardEl.querySelectorAll("[data-field]").forEach(el=>el.oninput=()=>{
    const card=el.closest("[data-scenario]");const idea=find(el.closest("[data-idea]").dataset.idea);const scenario=idea&&idea.scenarios.find(x=>x.id===card.dataset.scenario);if(!scenario)return;
    if(el.dataset.field==="acceptance")scenario.acceptance=el.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);else scenario[el.dataset.field]=el.value;
    syncScenario(idea,scenario);
    const json=boardEl.querySelector(`[data-idea-json="${idea.id}"]`);if(json)json.value=idea.jsonText;
  });
  boardEl.querySelectorAll("[data-delete-idea]").forEach(el=>el.onclick=()=>{if(!confirm("Удалить идею вместе со всеми сценариями?"))return;board.ideas=board.ideas.filter(x=>x.id!==el.dataset.deleteIdea);if(board.activeId===el.dataset.deleteIdea)board.activeId=board.ideas[0]?.id||"";render();save()});
  boardEl.querySelectorAll("[data-delete-scenario]").forEach(el=>el.onclick=()=>{const idea=find(el.closest("[data-idea]").dataset.idea);if(!idea)return;idea.scenarios=idea.scenarios.filter(x=>x.id!==el.dataset.deleteScenario);idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);render();save();toast("Сценарий удалён из ветки и JSON")});
  boardEl.querySelectorAll("[data-to-plan]").forEach(el=>el.onclick=()=>sendToConstructor(el));
  boardEl.querySelectorAll("[data-generate-prompt]").forEach(el=>el.onclick=()=>generatePrompt(el.dataset.generatePrompt,el));
  boardEl.querySelectorAll("[data-copy-prompt]").forEach(el=>el.onclick=()=>copy(find(el.dataset.copyPrompt)?.prompt||""));
  boardEl.querySelectorAll("[data-import-json]").forEach(el=>el.onclick=()=>importJson(el.dataset.importJson));
  boardEl.querySelectorAll("[data-add-first]").forEach(el=>el.onclick=addIdea);
  list.querySelectorAll("[data-focus-idea]").forEach(el=>el.onclick=()=>{board.activeId=el.dataset.focusIdea;render();save()});
}
function find(ideaId){return board.ideas.find(x=>x.id===ideaId)}
function updatePromptState(idea){
  const fresh=!!idea.prompt&&idea.promptBasis===promptBasis(idea);
  const copyButton=document.querySelector(`[data-copy-prompt="${idea.id}"]`);if(copyButton)copyButton.disabled=!fresh;
  const state=document.querySelector(`[data-prompt-state="${idea.id}"]`);if(state){state.textContent=fresh?"Актуален":idea.prompt?"Устарел":"";state.classList.toggle("fresh",fresh);state.classList.toggle("stale",!!idea.prompt&&!fresh)}
}
async function sendToConstructor(button){
  const idea=find(button.closest("[data-idea]").dataset.idea);const scenario=idea&&idea.scenarios.find(item=>item.id===button.dataset.toPlan);if(!scenario)return;
  if(!(await setTab("scenario")))return;
  S.createState=normalizeDraft({...S.createState,title:scenario.title,description:scenario.description,acceptance:scenario.acceptance.join("\n"),domain:scenario.domain,http_method:scenario.source.http_method||"",path:scenario.source.path||"",slug:scenario.source.slug||"",uses_db:scenario.source.uses_db!==false,with_test:scenario.source.with_test!==false});
  schedulePersist();document.querySelector('[data-workspace="plans"]')?.click();document.getElementById("planTitle")?.focus();toast("Сценарий перенесён в форму плана");
}
function updateRail(){const list=$("ideaList");if(!list)return;list.querySelectorAll(".idea-rail-item").forEach(el=>{const item=find(el.dataset.focusIdea);if(item){el.querySelector("b").textContent=item.title||"Без названия";el.querySelector("small").textContent=`${item.scenarios.length} сценариев`}})}
async function generatePrompt(ideaId,button){
  const idea=find(ideaId);if(!idea)return;if(!idea.description.trim()){toast("Сначала опишите идею");return}
  button.disabled=true;button.textContent="Собираю…";
  try{const data=await api("/api/decompose/prompt","POST",{task:`${idea.title}\n\n${idea.description}`});idea.prompt=data.prompt||"";idea.promptBasis=promptBasis(idea);render();save();toast("Промпт готов")}
  catch(error){toast("Не удалось собрать промпт: "+error.message);button.disabled=false;button.textContent="✦ Собрать промпт"}
}
function importJson(ideaId){
  const idea=find(ideaId);if(!idea)return;
  try{const parsed=JSON.parse(idea.jsonText);const scenarios=normalizedScenarios(parsed);if(idea.scenarios.length&&!confirm("Заменить сценарии этой ветки? Текущие правки карточек будут перезаписаны."))return;idea.payload=Array.isArray(parsed)?{scenarios:parsed}:parsed;idea.scenarios=scenarios;idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);render();save();toast(`Добавлено сценариев: ${scenarios.length}`)}
  catch(error){toast("JSON не разобран: "+error.message)}
}
function copy(value){if(!value){toast("Сначала соберите промпт");return}if(navigator.clipboard?.writeText)navigator.clipboard.writeText(value).then(()=>toast("Промпт скопирован")).catch(()=>toast("Не удалось скопировать"));else{const el=document.createElement("textarea");el.value=value;document.body.appendChild(el);el.select();document.execCommand("copy");el.remove();toast("Промпт скопирован")}}
function bind(){
  $("addIdeaBtn")?.addEventListener("click",addIdea);$("addIdeaEmpty")?.addEventListener("click",addIdea);
  $("ideasExportBtn")?.addEventListener("click",exportBoard);
  $("ideasImportBtn")?.addEventListener("click",()=>$("ideasImportFile")?.click());
  $("ideasImportFile")?.addEventListener("change",importBoard);
}
function exportBoard(){
  const blob=new Blob([JSON.stringify(board,null,2)],{type:"application/json;charset=utf-8"});
  const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download="studio-ideas.json";document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),1000);toast("Доска идей скачана")
}
async function importBoard(event){
  const input=event.currentTarget,file=input.files&&input.files[0];if(!file)return;
  try{const incoming=normalizeBoard(JSON.parse(await file.text()));if(board.ideas.length&&!confirm("Заменить текущую доску идеями из файла? Текущие идеи будут перезаписаны."))return;board=incoming;render();save();toast(`Загружено идей: ${board.ideas.length}`)}
  catch(error){toast("Не удалось загрузить доску: "+error.message)}
  finally{input.value=""}
}
export function renderIdeas(){if(loadedKey!==key())load();render()}
export function initIdeas(){load();bind();render();if(!lifecycleBound){window.addEventListener("pagehide",flushSave);lifecycleBound=true}}

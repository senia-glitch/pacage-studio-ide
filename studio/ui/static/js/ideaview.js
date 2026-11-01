import {api} from "./api.js";
import {S, esc, setTab, schedulePersist, toast} from "./core.js";
import {normalizeDraft} from "./chainmodel.js";
import {storageKey} from "./storage.js";

const EMPTY = {version: 1, ideas: []};
let board = EMPTY;
let persistTimer = null;
let loadedKey = "";

function key(){ return `${storageKey((S.G&&S.G.project_root)||location.origin)}:ideas:v1`; }
function save(){
  clearTimeout(persistTimer);
  const state=$("ideasSaveState"); if(state)state.textContent="Сохранение…";
  persistTimer=setTimeout(()=>{
    try{localStorage.setItem(loadedKey,JSON.stringify(board));if(state)state.textContent="Все изменения сохранены"}
    catch(_){if(state)state.textContent="Не удалось сохранить"}
  },220);
}
function load(){
  loadedKey=key();
  try{const data=JSON.parse(localStorage.getItem(loadedKey)||"null");board=data&&Array.isArray(data.ideas)?data:structuredClone(EMPTY)}
  catch(_){board=structuredClone(EMPTY)}
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
  const parsed=idea.payload&&typeof idea.payload==="object"?structuredClone(idea.payload):{scenarios:[]};
  if(Array.isArray(parsed))return idea.scenarios.map(s=>({...s.source}));
  parsed.scenarios=idea.scenarios.map(s=>({...s.source}));
  return parsed;
}
function syncScenario(idea,scenario){
  scenario.source={...scenario.source,title:scenario.title,description:scenario.description,acceptance:scenario.acceptance,domain:scenario.domain};
  idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);save();
}
function addIdea(){
  const idea={id:id(),title:"Новая идея",description:"",prompt:"",jsonText:"",payload:{scenarios:[]},scenarios:[]};
  board.ideas.push(idea);render();save();
  const input=document.querySelector(`[data-idea-title="${idea.id}"]`);if(input){input.focus();input.select()}
}
function card(idea){
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
      <div class="idea-node-actions"><button type="button" class="primary" data-generate-prompt="${esc(idea.id)}">✦ Собрать промпт</button><button type="button" data-copy-prompt="${esc(idea.id)}" ${idea.prompt?"":"disabled"}>Копировать</button></div>
      <details class="idea-prompt-details" ${idea.prompt?"":""}><summary>Промпт для LLM</summary><textarea data-idea-prompt="${esc(idea.id)}" rows="7" readonly placeholder="Соберите промпт на основе идеи">${esc(idea.prompt)}</textarea></details>
      <div class="idea-json-head"><label class="idea-field-label">ОТВЕТ LLM · JSON</label><button type="button" class="text-button" data-import-json="${esc(idea.id)}">Разобрать сценарии ↓</button></div>
      <textarea class="idea-json" data-idea-json="${esc(idea.id)}" rows="7" placeholder='Вставьте JSON с массивом "scenarios"'>${esc(idea.jsonText)}</textarea>
      <div class="idea-json-foot">${idea.scenarios.length?`${idea.scenarios.length} сценариев в ветке`:'Ответ ещё не разобран'}</div>
    </article>
    <div class="idea-branch"><div class="branch-label">СЦЕНАРИИ <span>${idea.scenarios.length}</span></div><div class="scenario-branch">${children||'<div class="branch-empty"><span>＋</span><b>Ветка пока пустая</b><small>Вставьте JSON от LLM и разберите ответ</small></div>'}</div></div>
  </section>`;
}
function render(){
  const boardEl=$("ideasBoard"),list=$("ideaList");if(!boardEl||!list)return;
  boardEl.innerHTML=board.ideas.length?board.ideas.map(card).join(""):'<div class="ideas-welcome"><span>✳</span><h3>Начните с идеи</h3><p>Создайте корневой узел, сформируйте промпт для LLM и добавьте сценарии в виде связанной ветки.</p><button class="primary" type="button" data-add-first>＋ Создать первую идею</button></div>';
  list.innerHTML=board.ideas.length?board.ideas.map((idea,i)=>`<button type="button" class="idea-rail-item" data-focus-idea="${esc(idea.id)}"><span>${String(i+1).padStart(2,"0")}</span><b>${esc(idea.title||"Без названия")}</b><small>${idea.scenarios.length} сценариев</small></button>`).join(""):'<p class="rail-empty">Пока нет идей</p>';
  boardEl.querySelectorAll("[data-idea-title]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaTitle);if(!idea)return;idea.title=el.value;updateRail();save()});
  boardEl.querySelectorAll("[data-idea-description]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaDescription);if(idea){idea.description=el.value;save()}});
  boardEl.querySelectorAll("[data-idea-json]").forEach(el=>el.oninput=()=>{const idea=find(el.dataset.ideaJson);if(idea)idea.jsonText=el.value;save()});
  boardEl.querySelectorAll("[data-field]").forEach(el=>el.oninput=()=>{
    const card=el.closest("[data-scenario]");const idea=find(el.closest("[data-idea]").dataset.idea);const scenario=idea&&idea.scenarios.find(x=>x.id===card.dataset.scenario);if(!scenario)return;
    if(el.dataset.field==="acceptance")scenario.acceptance=el.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);else scenario[el.dataset.field]=el.value;
    syncScenario(idea,scenario);
    const json=boardEl.querySelector(`[data-idea-json="${idea.id}"]`);if(json)json.value=idea.jsonText;
  });
  boardEl.querySelectorAll("[data-delete-idea]").forEach(el=>el.onclick=()=>{if(!confirm("Удалить идею вместе со всеми сценариями?"))return;board.ideas=board.ideas.filter(x=>x.id!==el.dataset.deleteIdea);render();save()});
  boardEl.querySelectorAll("[data-delete-scenario]").forEach(el=>el.onclick=()=>{const idea=find(el.closest("[data-idea]").dataset.idea);if(!idea)return;idea.scenarios=idea.scenarios.filter(x=>x.id!==el.dataset.deleteScenario);idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);render();save();toast("Сценарий удалён из ветки и JSON")});
  boardEl.querySelectorAll("[data-to-plan]").forEach(el=>el.onclick=()=>sendToConstructor(el));
  boardEl.querySelectorAll("[data-generate-prompt]").forEach(el=>el.onclick=()=>generatePrompt(el.dataset.generatePrompt,el));
  boardEl.querySelectorAll("[data-copy-prompt]").forEach(el=>el.onclick=()=>copy(find(el.dataset.copyPrompt)?.prompt||""));
  boardEl.querySelectorAll("[data-import-json]").forEach(el=>el.onclick=()=>importJson(el.dataset.importJson));
  boardEl.querySelectorAll("[data-add-first]").forEach(el=>el.onclick=addIdea);
  list.querySelectorAll("[data-focus-idea]").forEach(el=>el.onclick=()=>{document.querySelector(`[data-idea="${el.dataset.focusIdea}"]`)?.scrollIntoView({behavior:"smooth",block:"start"});list.querySelectorAll(".idea-rail-item").forEach(x=>x.classList.toggle("on",x===el))});
}
function find(ideaId){return board.ideas.find(x=>x.id===ideaId)}
async function sendToConstructor(button){
  const idea=find(button.closest("[data-idea]").dataset.idea);const scenario=idea&&idea.scenarios.find(item=>item.id===button.dataset.toPlan);if(!scenario)return;
  S.createState=normalizeDraft({...S.createState,title:scenario.title,description:scenario.description,acceptance:scenario.acceptance.join("\n"),domain:scenario.domain,http_method:scenario.source.http_method||"",path:scenario.source.path||"",slug:scenario.source.slug||"",uses_db:scenario.source.uses_db!==false,with_test:scenario.source.with_test!==false});
  schedulePersist();await setTab("scenario");document.querySelector('[data-workspace="plans"]')?.click();document.getElementById("planTitle")?.focus();toast("Сценарий перенесён в форму плана");
}
function updateRail(){const list=$("ideaList");if(!list)return;const idea=board.ideas;list.querySelectorAll(".idea-rail-item").forEach((el,i)=>{const item=idea[i];if(item){el.querySelector("b").textContent=item.title||"Без названия";el.querySelector("small").textContent=`${item.scenarios.length} сценариев`}})}
async function generatePrompt(ideaId,button){
  const idea=find(ideaId);if(!idea)return;if(!idea.description.trim()){toast("Сначала опишите идею");return}
  button.disabled=true;button.textContent="Собираю…";
  try{const data=await api("/api/decompose/prompt","POST",{task:`${idea.title}\n\n${idea.description}`});idea.prompt=data.prompt||"";render();save();toast("Промпт готов")}
  catch(error){toast("Не удалось собрать промпт: "+error.message);button.disabled=false;button.textContent="✦ Собрать промпт"}
}
function importJson(ideaId){
  const idea=find(ideaId);if(!idea)return;
  try{const parsed=JSON.parse(idea.jsonText);const scenarios=normalizedScenarios(parsed);idea.payload=Array.isArray(parsed)?{scenarios:parsed}:parsed;idea.scenarios=scenarios;idea.payload=payloadFor(idea);idea.jsonText=JSON.stringify(idea.payload,null,2);render();save();toast(`Добавлено сценариев: ${scenarios.length}`)}
  catch(error){toast("JSON не разобран: "+error.message)}
}
function copy(value){if(!value){toast("Сначала соберите промпт");return}if(navigator.clipboard?.writeText)navigator.clipboard.writeText(value).then(()=>toast("Промпт скопирован")).catch(()=>toast("Не удалось скопировать"));else{const el=document.createElement("textarea");el.value=value;document.body.appendChild(el);el.select();document.execCommand("copy");el.remove();toast("Промпт скопирован")}}
function bind(){
  $("addIdeaBtn")?.addEventListener("click",addIdea);$("addIdeaEmpty")?.addEventListener("click",addIdea);
}
export function renderIdeas(){if(loadedKey!==key())load();render()}
export function initIdeas(){load();bind();render()}

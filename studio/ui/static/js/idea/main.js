/* Этап «Идея» внутри студии: интерфейс живёт в Shadow DOM вкладки (свои стили и id).
   Подключается при первом открытии вкладки; дальше вкладка только обновляется. */

import {initBank, renderBank} from "./bank.js";
import {$, S, act, api, esc, on, toast} from "./core.js";
import {setDomRoot} from "./dom.js";
import {fitGraph, initGraph, loadGraph, resetGraphView} from "./graph.js";
import {initSteps, renderSteps} from "./steps.js";

const TEMPLATE = `
<link rel="stylesheet" href="/static/idea.css">
<div class="idea-app">
  <header>
    <span class="logo">Идея</span>
    <span id="projectTitle" class="project-title"></span>
    <nav class="tabs" id="tabs"></nav>
    <span class="spacer"></span>
    <span id="saveState" class="hint"></span>
    <span id="zoomLabel" class="hint">100%</span>
    <button id="composeBtn" title="Расставить узлы так, чтобы потомки стояли рядом с родителями">Скомпоновать</button>
    <button id="undoComposeBtn" style="display:none" title="Вернуть расстановку, какая была до компоновки">Отменить</button>
    <button id="fitBtn" title="Вписать граф в окно (двойной щелчок по фону)">Вписать граф</button>
    <button id="exportBtn" title="Сразу записать Документация.md по текущему графу, без проверки">Выгрузить .md</button>
    <button id="checkBtn" title="Независимая модель проверит граф и задаст вопросы по каждому разногласию, потом запишет Документация.md">Проверить и выгрузить</button>
    <button id="exportAutoBtn" title="То же без вопросов: вне границ и спорное убирается само, потом запишет Документация.md">Проверить и выгрузить автоматически</button>
  </header>
  <main>
    <section class="tab" id="tab-work">
      <aside class="steps-panel" id="steps"></aside>
      <div id="canvas"></div>
    </section>
    <section class="tab" id="tab-bank"><div id="bankPage" class="scroll"></div></section>
    <section class="tab" id="tab-settings"><div class="page" id="settingsPage"></div></section>
    <section class="tab on" id="tab-welcome"><div class="page" id="welcomePage"></div></section>
  </main>
  <div id="toast" class="toast" hidden></div>
</div>`;

let shadow = null;
let tab = "welcome";
let seen = -1;
let polling = false;

function tabs() {
  const items = S.project ? [["work", "Работа"], ["bank", "Банк"]] : [["welcome", "Начало"]];
  $("tabs").innerHTML = items.map(([id, name]) => `<button class="tabbtn${tab === id ? " on" : ""}" data-tab="${id}">${esc(name)}</button>`).join("");
}

async function show(next) {
  tab = next;
  shadow.querySelectorAll("main > .tab").forEach((el) => el.classList.toggle("on", el.id === "tab-" + next));
  for (const id of ["zoomLabel", "fitBtn", "composeBtn", "exportBtn", "checkBtn", "exportAutoBtn"]) $(id).style.display = next === "work" ? "" : "none";
  tabs();
  if (next === "welcome") renderWelcome();
  if (next === "bank") renderBank(true);
  if (next === "work") { renderSteps(true); setTimeout(fitGraph, 80); }
}

function renderWelcome() {
  $("welcomePage").innerHTML = `
    <h2>Этап «Идея»</h2>
    <p class="hint">Из сырой идеи получается проверенная документация: конкуренты, роли и компоненты, сценарии, проблемы и решения.
      Агенты помогают, решения принимаете вы. Результат — файл <b>Документация.md</b>: от него дальше отталкиваются идеи-карточки студии и ТЗ.</p>
    <form id="createForm" class="form-grid one">
      <label>Название сферы или идеи<input name="title" required maxlength="120" placeholder="Например: автоматизация парикмахерской"></label>
      <div class="row-actions"><button class="primary">Начать</button></div>
    </form>
    <p class="hint">Агенты и модели для этого этапа выбираются в «Настройки → Агенты». Граф и документация хранятся в <code>${esc(S.boot?.folder || "")}</code>.</p>`;
  $("createForm").onsubmit = (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    act(async () => { await api("/api/project/create", data); await openProject(); });
  };
}

async function loadAll() {
  const [, bank] = await Promise.all([loadGraph(), api("/api/bank")]);
  S.bank = bank.records;
}

async function openProject() {
  S.boot = await api("/api/boot");
  S.project = S.boot.project;
  seen = -1;
  resetGraphView();
  S.step = 0;
  if (!S.project) { tabs(); return show("welcome"); }
  $("projectTitle").textContent = S.project.title;
  await loadAll();
  seen = S.project.version;
  S.step = firstOpenStep();
  await show("work");
  const warnings = S.project.warnings || [];
  if (warnings.length) toast("Внимание: " + warnings.join(" • "), 12000);
}

/* При открытии проекта показываем первый шаг, где ещё нет результата. */
function firstOpenStep() {
  const has = [S.graph.cards.some((c) => c.kind === "сфера"), S.bank.some((r) => r.type === "competitor"), S.bank.some((r) => r.type === "fact"),
    S.graph.cards.some((c) => c.kind === "роль"), S.graph.cards.some((c) => c.kind === "сценарий"), S.graph.cards.some((c) => c.kind === "как есть"),
    S.graph.cards.some((c) => c.kind === "решение")];
  const i = has.findIndex((x) => !x);
  return i < 0 ? has.length - 1 : i;
}

/* Опрос состояния только пока открыта вкладка «Идея»: в других вкладках студии запросов нет. */
async function poll() {
  if (polling || !S.project || document.body.dataset.activeTab !== "idea") return;
  polling = true;
  let state, changed;
  try {
    state = await api("/api/state");
    changed = state.version !== seen || state.revision !== S.project.revision;
    S.project = { ...state, warnings: S.project.warnings };
    if (changed) { seen = state.version; await loadAll(); }
    $("saveState").textContent = "";
  } catch (error) {
    // связи нет — только если студия не ответила; ответ с ошибкой — другое дело
    $("saveState").textContent = error.code ? "ошибка студии: " + error.message : "нет связи со студией";
    polling = false;
    return;
  }
  try {
    $("projectTitle").textContent = state.title;
    if (tab === "work") renderSteps(changed);
    if (tab === "bank") renderBank(changed);
  } catch (error) {
    $("saveState").textContent = "ошибка отображения: " + (error.message || error);   // связь есть, сломалась отрисовка
  } finally {
    polling = false;
  }
}

async function mount(host) {
  shadow = host.shadowRoot || host.attachShadow({mode: "open"});
  shadow.innerHTML = TEMPLATE;
  setDomRoot(shadow);
  $("tabs").onclick = (e) => { const id = e.target.dataset.tab; if (id) show(id); };
  initGraph();
  initSteps();
  initBank();
  on("poll", poll);
  try {
    await openProject();
  } catch (error) {
    $("welcomePage").innerHTML = `<div class="err">${esc(error.message)}</div>`;
  }
  setInterval(poll, 1000);
}

/* Вызывается студией каждый раз, когда открыта вкладка «Идея». */
export async function renderIdea(host) {
  if (!shadow) return mount(host);
  if (S.project) {
    await poll();
    if (tab === "work") setTimeout(fitGraph, 80);        // вкладка была скрыта: карточки не измерялись
  }
  return undefined;
}

import {api} from "./api.js";
import {$, esc, setTab, toast} from "./core.js";
import {
  autoKey, defaultSelection, progressOf,
  selectionCount, selectionPayload, testsView,
} from "./roadmapmodel.js";
import {
  agentStatus, agentTitle, canRollback, dockActions, dockNote, dockSteps, gitNote,
  projectButton, serverRows, serversBusy,
} from "./dockmodel.js";
import {closeSwipe, isSwipeOpen, openSwipe, refreshSwipe} from "./swipeview.js";

/* Окно агента: связь, 7 шагов цепочки («чей ход»), главная кнопка шага и окна выбора задач и отчёта.
   Разбор карточек идёт свайпом на странице «Генератор идей», подключение — в «Настройках агента». */

const POLL_MS = 4000;
const OPENED_KEY = "studio:roadmap-opened";
let flow = null;
let agent = null; // GET /api/agent/status: связь, состояние запуска, вопрос, git
let goalDraft = {text: "", skipIdeas: false};
let answerDraft = "";
let timer = null;
let progress = null;
let busy = false;
let hooks = {};
let servers = null; // GET /api/devservers: серверы проекта (бэкенд, фронтенд)
let serversLogOpen = false;
let serversTimer = null;

function opened() {
  try { return new Set(JSON.parse(sessionStorage.getItem(OPENED_KEY) || "[]")); }
  catch (_) { return new Set(); }
}

function remember(key) {
  const set = opened();
  set.add(key);
  try { sessionStorage.setItem(OPENED_KEY, JSON.stringify([...set].slice(-40))); } catch (_) { /* без хранилища */ }
}

export function currentFlow() { return flow; }

/* ----------------------------------------------------------------- окна */

function dialog(title, bodyHtml, className = "") {
  document.querySelectorAll(".studio-modal").forEach((el) => el.remove());
  const overlay = document.createElement("div");
  overlay.className = "studio-modal rm-modal";
  overlay.innerHTML = `<div class="modal-box rm-box ${className}"><h3>${esc(title)}</h3><div class="modal-body">${bodyHtml}</div><div class="modal-actions"></div></div>`;
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const actions = overlay.querySelector(".modal-actions");
  const api2 = {
    box: overlay.querySelector(".modal-box"),
    body: overlay.querySelector(".modal-body"),
    close: () => overlay.remove(),
    button(label, handler, cls = "") {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.className = cls;
      button.onclick = () => handler(button);
      actions.appendChild(button);
      return button;
    },
  };
  return api2;
}

async function post(path, body = {}) {
  try { return await api(path, "POST", body); }
  catch (error) { toast(error.message); return null; }
}

async function apply(result) {
  if (result) { flow = result; render(); }
  await refreshRoadmap();
}

/* --- этап 6: выбор задач на реализацию */

function group(title, items, kind, key, labelOf, selection) {
  if (!items.length) return "";
  const rows = items.map((item) => `<label class="rm-check${item.ready ? "" : " off"}">
      <input type="checkbox" data-kind="${kind}" data-key="${esc(item[key])}"${selection[kind].has(item[key]) ? " checked" : ""}${item.ready ? "" : " disabled"}>
      <span>${labelOf(item)}${item.ready ? "" : '<small class="rm-warn">нет описания — заполните на карте</small>'}</span></label>`).join("");
  return `<div class="rm-group"><div class="rm-group-title">${esc(title)} <span>${items.length}</span></div>${rows}</div>`;
}

async function openSelect() {
  let candidates;
  try { candidates = await api("/api/workflow/candidates"); }
  catch (error) { toast(error.message); return; }
  const selection = defaultSelection(candidates);
  const total = candidates.plans.length + candidates.nodes.length + candidates.pages.length;
  if (!total) {
    const win = dialog("Нечего реализовывать", `<p>На карте нет запланированных задач. Начните с «Планирования» — агент предложит идеи.</p>`);
    win.button("Начать заново", async () => { win.close(); await apply(await post("/api/workflow/stage", {to: "mode"})); }, "primary");
    return;
  }
  const win = dialog("Что отдать агенту на реализацию?", `
    ${group("Сценарии бэкенда", candidates.plans, "plans", "slug",
      (p) => `<b>${esc(p.title)}</b> <code>${esc(p.slug)}</code>${p.route ? ` <small>${esc(p.route)}</small>` : ""}`, selection)}
    ${group("Другие плановые узлы", candidates.nodes, "nodes", "id",
      (n) => `<b>${esc(n.label)}</b> <small>${esc(n.kind)}</small>`, selection)}
    ${group("Страницы фронтенда", candidates.pages, "pages", "id",
      (p) => (p.new
        ? `<b>${esc(p.name)}</b> <small>${esc(p.route)}${p.blocks ? ` · блоков: ${p.blocks}` : ""}</small>`
        : `<b>${esc(p.name)}</b> <small>новые блоки: ${esc((p.planned_blocks || []).join(", "))}</small>`), selection)}
    <div class="rm-selected" id="rmSelected"></div>`);
  const go = win.button("Отдать агенту", async (button) => {
    button.disabled = true;
    const result = await post("/api/workflow/select", selectionPayload(selection));
    if (result) { win.close(); toast("ТЗ сохранено: агент приступает к реализации"); await apply(result); }
    else button.disabled = false;
  }, "primary");
  const refresh = () => {
    const count = selectionCount(selection);
    win.body.querySelector("#rmSelected").textContent = `Выбрано задач: ${count}`;
    go.disabled = count === 0;
    go.textContent = count ? `Отдать агенту (${count})` : "Отдать агенту";
  };
  win.body.querySelectorAll("input[data-kind]").forEach((input) => {
    input.onchange = () => {
      const set = selection[input.dataset.kind];
      if (input.checked) set.add(input.dataset.key); else set.delete(input.dataset.key);
      refresh();
    };
  });
  win.button("Все готовые", () => {
    const fresh = defaultSelection(candidates);
    Object.assign(selection, fresh);
    win.body.querySelectorAll("input[data-kind]:not(:disabled)").forEach((input) => { input.checked = true; });
    refresh();
  });
  win.button("Снять все", () => {
    for (const set of Object.values(selection)) set.clear();
    win.body.querySelectorAll("input[data-kind]").forEach((input) => { input.checked = false; });
    refresh();
  });
  win.button("Позже", win.close);
  refresh();
}

/* --- этапы 7–8: реализация и отчёт */

async function verifyParts() {
  const briefs = (flow && flow.briefs) || {};
  const jobs = [];
  if (briefs.backend !== null && briefs.backend !== undefined) jobs.push(api("/api/verify?brief=last").catch(() => null));
  if (briefs.frontend !== null && briefs.frontend !== undefined) jobs.push(api("/api/frontend/verify?brief=last").catch(() => null));
  return Promise.all(jobs);
}

async function updateProgress() {
  if (!flow || flow.stage !== "implement") { progress = null; return; }
  progress = progressOf(await verifyParts());
}

function statusIcon(status) {
  return status === "выполнено" ? "✓" : status === "частично" ? "◐" : "✗";
}

async function openReport() {
  const reports = await verifyParts();
  const done = progressOf(reports);
  const items = reports.flatMap((report) => (report && report.items) || []);
  const rows = items.map((item) => `<li class="${item.status === "выполнено" ? "ok" : "no"}">
      <span>${statusIcon(item.status)}</span> <b>${esc(item.anchor || item.name || item.slug || "")}</b>
      ${item.status === "выполнено" ? "" : `<small>${esc(item.status)}${item.message ? " — " + esc(item.message) : ""}</small>`}</li>`).join("");
  let changes = 0;
  if (flow && flow.report_id) {
    try {
      const record = await api("/api/agent/report?id=" + encodeURIComponent(flow.report_id));
      changes = (record.counts && record.counts.change) || 0;
    } catch (_) { /* отчёта может не быть: работа принята вручную */ }
  }
  const summary = (flow && flow.summary) || "Агент не оставил текста отчёта.";
  let tests = null;
  try { tests = await api("/api/tests"); } catch (_) { /* нет связи: блок покажет «не запускала» */ }
  const win = dialog("Отчёт агента", `
    <div class="rm-summary">${esc(summary)}</div>
    ${done.total ? `<div class="rm-progress${done.complete ? " ok" : ""}">Проверка по коду: выполнено ${done.done} из ${done.total}</div>` : ""}
    ${rows ? `<ul class="rm-results">${rows}</ul>` : ""}
    <div id="rmTests"></div>
    ${changes ? `<p class="hint">Агент предложил обновить описания узлов (${changes}). Проверьте и примите нужное — ничего не записано без вашего «Применить».</p>` : ""}`, "rm-report");
  const drawTests = async () => {
    const box = win.body.querySelector("#rmTests");
    if (!box || !document.body.contains(box)) return;
    const view = testsView(tests);
    const bad = (view.failures || []).map((item) =>
      `<li><b>${esc(item.name)}</b> <small>${esc(item.file || "")}${item.message ? " — " + esc(String(item.message).split("\n")[0]) : ""}</small></li>`).join("");
    const plans = Object.entries((tests && tests.plans) || {}).map(([slug, names]) =>
      `<li>план <b>${esc(slug)}</b>: упало тестов ${names.length}</li>`).join("");
    box.innerHTML = `<div class="rm-tests ${view.tone}"><b>${esc(view.text)}</b>${bad ? `<ul>${bad}</ul>` : ""}${plans ? `<ul>${plans}</ul>` : ""}
      <button type="button" id="rmRunTests"${view.tone === "run" ? " disabled" : ""}>Запустить тесты проекта</button></div>`;
    box.querySelector("#rmRunTests").onclick = async () => {
      tests = await post("/api/tests/run") || tests;
      await drawTests();
    };
    if (view.tone === "run") {
      setTimeout(async () => {
        try { tests = await api("/api/tests"); } catch (_) { return; }
        await drawTests();
      }, 2000);
    }
  };
  await drawTests();
  if (changes) {
    win.button(`Описания от агента (${changes})…`, async () => {
      win.close();
      if (hooks.openReport) await hooks.openReport(flow.report_id);
    });
  }
  win.button("Вернуть к выбору задач", async () => { win.close(); await apply(await post("/api/workflow/stage", {to: "select"})); });
  win.button("Закрыть", win.close);
  win.button("Всё хорошо — принять", async () => {
    win.close();
    await finishCycle();
  }, "primary");
}

function confirmReset() {
  const win = dialog("Начать заново?", `<p>Текущая цепочка будет сброшена. Карточки, карта и код остаются на месте — сбрасывается только этап.</p>`);
  win.button("Отмена", win.close);
  win.button("Начать заново", async () => { win.close(); await apply(await post("/api/workflow/stage", {to: "mode"})); }, "primary");
}

function confirmManual() {
  const win = dialog("Принять работу без отчёта?", `<p>Агент ещё не прислал отчёт. Если вы уже видите результат и отчёт не нужен, перейдите к этапу «Отчёт» вручную.</p>`);
  win.button("Подождать агента", win.close);
  win.button("Принять без отчёта", async () => { win.close(); await apply(await post("/api/workflow/stage", {to: "report"})); }, "primary");
}

/* -------------------------------------------------------------- действия */

const COLLAPSE_KEY = "studio:dock-collapsed";

function collapsed() {
  try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch (_) { return false; }
}

function setCollapsed(value) {
  try { localStorage.setItem(COLLAPSE_KEY, value ? "1" : "0"); } catch (_) { /* без хранилища */ }
}

async function toSelect() {
  if (flow && flow.stage === "review") {
    const moved = await post("/api/workflow/stage", {to: "confirm"});
    if (!moved) return;
  }
  const result = await post("/api/workflow/transfer");
  const errors = (result && result.transfer && result.transfer.errors) || [];
  if (errors.length) {
    toast("Не удалось перенести: " + errors.map((item) => `${item.title} — ${item.message}`).join("; "));
    await refreshRoadmap();
    return;
  }
  await apply(result);
}

function openRework() {
  const win = dialog("Вернуть на доработку", `
    <p>Агент заново возьмёт то же ТЗ и учтёт ваше пожелание. Если оставить пустым, он сам разберёт результаты тестов.</p>
    <label class="rm-task">Что поправить
      <textarea id="rmReworkNote" rows="4" placeholder="Например: не хватает проверки пустого запроса"></textarea></label>`);
  win.button("Отмена", win.close);
  win.button("Вернуть агенту", async () => {
    const note = win.body.querySelector("#rmReworkNote").value;
    win.close();
    await apply(await post("/api/workflow/rework", {note}));
  }, "primary");
}

function openRollback() {
  const win = dialog("Откатить работу агента?", `
    <p>Проект вернётся к состоянию до запуска агента: правки агента и созданные им файлы исчезнут.
    Откат работает только в ветке <code>studio/*</code>; ваши коммиты в других ветках не затрагиваются.</p>`);
  win.button("Отмена", win.close);
  win.button("Откатить", async () => {
    win.close();
    const result = await post("/api/git/rollback");
    if (result) toast("Проект возвращён к состоянию до работы агента");
    await refreshRoadmap();
  }, "primary");
}

async function finishCycle() {
  if (hooks.onAccepted) {
    try { await hooks.onAccepted(); } catch (_) { /* пометка «реализовано» не должна мешать приёмке */ }
  }
  await apply(await post("/api/workflow/finish"));
  toast("Цикл завершён. Можно ставить новую цель.");
}

async function answerAgent(text) {
  const reply = String(text || "").trim();
  if (!reply) { toast("Выберите вариант или напишите ответ"); return; }
  const result = await post("/api/agent/answer", {text: reply});
  if (result) {
    answerDraft = ""; // при отказе сервера черновик остаётся: ответ не пропадает
    toast("Ответ отправлен: студия запускает агента");
  }
  await refreshRoadmap();
}

/* --- серверы проекта: кнопки блока «Проект» */

async function loadServers() {
  try { servers = await api("/api/devservers"); } catch (_) { servers = null; }
}

/* Быстрый опрос, пока сервер запускается (ссылка появится сразу), и редкий — в остальное время. */
function scheduleServers() {
  clearTimeout(serversTimer);
  serversTimer = setTimeout(async () => {
    await loadServers();
    render();
    scheduleServers();
  }, serversBusy(servers) ? 1200 : 5000);
}

async function serverCommand(act, name) {
  const result = await post(`/api/devservers/${act}`, {name});
  if (result) { servers = {...servers, ...result}; render(); }
  scheduleServers();
}

function serversBlock() {
  const rows = serverRows(servers);
  if (!rows.length) return "";
  const main = projectButton(servers);
  const dots = {stopped: "", starting: "run", running: "ok", failed: "bad"};
  const items = rows.map((row) => `<div class="srv-row ${esc(row.state)}">
      <span class="dock-dot ${dots[row.state]}"></span>
      <b>${esc(row.title)}</b>
      <span class="srv-text">${esc(row.text)}</span>
      ${row.canStop ? `<button type="button" data-srv="stop" data-name="${esc(row.name)}" title="Остановить">■</button>`
        : `<button type="button" data-srv="start" data-name="${esc(row.name)}" title="Запустить"${row.canStart ? "" : " disabled"}>▶</button>`}
      ${row.url ? `<a class="srv-link" href="${esc(row.url)}" target="_blank" rel="noopener">${esc(row.url)}</a>` : ""}
      ${row.note ? `<small class="hint srv-note">${esc(row.note)}</small>` : ""}</div>`).join("");
  const failed = rows.find((row) => row.tail.length && (row.state === "failed" || row.state === "starting"));
  const tail = failed
    ? `<details class="dock-log" data-srv-log${serversLogOpen ? " open" : ""}><summary>Вывод: ${esc(failed.title)}</summary><pre>${esc(failed.tail.join("\n"))}</pre></details>` : "";
  return `<div class="dock-servers">
      <div class="dock-servers-head"><b>Проект</b>${main ? `<button type="button" data-srv="${esc(main.id)}">${esc(main.label)}</button>` : ""}</div>
      ${items}${tail}</div>`;
}

function run(id) {
  if (id === "start") {
    const task = goalDraft.text.trim();
    const mode = goalDraft.skipIdeas ? "implement" : "plan";
    if (mode === "plan" && !task) { toast("Опишите цель: что нужно сделать"); return; }
    remember(autoKey(flow));
    post("/api/workflow/start", {mode, task: mode === "plan" ? task : ""}).then((result) => {
      goalDraft = {text: "", skipIdeas: false};
      return apply(result);
    });
  } else if (id === "settings") void setTab("agent");
  else if (id === "retry") post("/api/runner/run").then(() => refreshRoadmap());
  else if (id === "stop") post("/api/runner/stop").then(() => refreshRoadmap());
  else if (id === "to-review") post("/api/workflow/stage", {to: "review"}).then(apply);
  else if (id === "goto-ideas") void setTab("ideas");
  else if (id === "to-select") void toSelect();
  else if (id === "select") openSelect();
  else if (id === "report") openReport();
  else if (id === "rework") openRework();
  else if (id === "rollback") openRollback();
  else if (id === "finish") void finishCycle();
  else if (id === "reset") confirmReset();
}

/* ----------------------------------------------------------------- вид */

function render() {
  const root = $("roadmap");
  if (!root || !flow) return;
  const status = agentStatus(agent);
  const list = dockSteps(flow);
  const actions = dockActions(flow, agent);
  let note = dockNote(flow, agent);
  if (flow.stage === "implement" && progress && progress.total) {
    note = `Агент реализует · выполнено ${progress.done} из ${progress.total}`;
  }
  const open = !collapsed();
  const ask = agent && agent.ask;
  const goal = list.find((step) => step.state === "active" && step.id === "goal") && status.connected;
  const tail = agent && agent.log_tail && status.connected
    ? `<details class="dock-log"><summary>Лог агента</summary><pre>${esc(agent.log_tail)}</pre></details>` : "";
  const html = `
    <div class="dock-head" data-dock="toggle" title="${open ? "Свернуть" : "Развернуть"}">
      <span class="dock-dot ${esc(status.tone)}"></span>
      <b>Агент · ${esc(agentTitle(agent))}</b>
      <span class="dock-status">${esc(status.text)}</span>
      <span class="dock-toggle">${open ? "—" : "▢"}</span>
    </div>
    ${open ? `<div class="dock-body">
      <ol class="dock-steps">${list.map((step, index) => `<li class="dock-step ${esc(step.state)}" title="${esc(step.title)}: ${esc(step.turnLabel)}">
        <span class="dock-num">${step.state === "done" ? "✓" : step.state === "skipped" ? "–" : index + 1}</span>
        <span class="dock-name">${esc(step.title)}</span>
        ${step.state === "active" ? `<small>${esc(step.turnLabel)}</small>` : ""}</li>`).join("")}</ol>
      <div class="dock-note">${esc(note)}</div>
      ${goal ? `<label class="dock-goal"><span>Цель</span><textarea id="dockGoal" rows="3" placeholder="Например: личный сайт с портфолио, новостями и формой заявок">${esc(goalDraft.text)}</textarea>
        <label class="rm-check"><input type="checkbox" id="dockSkip"${goalDraft.skipIdeas ? " checked" : ""}><span>Без идей: сразу к реализации того, что уже на карте</span></label></label>` : ""}
      ${ask ? `<div class="dock-ask"><b>${esc(ask.question)}</b>
        ${(ask.options || []).length ? `<div class="dock-options">${ask.options.map((option) => `<button type="button" class="${option === answerDraft.trim() ? "selected" : ""}" aria-pressed="${option === answerDraft.trim()}" data-answer="${esc(option)}">${esc(option)}</button>`).join("")}</div>` : ""}
        <textarea id="dockAnswer" rows="2" placeholder="${(ask.options || []).length ? "Выберите вариант или напишите свой" : "Ваш ответ"}">${esc(answerDraft)}</textarea>
        <button type="button" class="primary" data-dock="send-answer">Ответить</button></div>` : ""}
      <div class="dock-actions">${actions.map((action) => `<button type="button" class="${action.primary ? "primary" : ""}" data-rm="${esc(action.id)}">${esc(action.label)}</button>`).join("")}</div>
      ${serversBlock()}
      ${tail}
      <div class="dock-foot">
        <span class="hint">${esc(gitNote(agent))}</span>
        <span class="dock-tools">
          ${status.tone === "run" ? '<button type="button" data-rm="stop" title="Остановить агента">■ Стоп</button>' : ""}
          ${canRollback(agent) ? '<button type="button" data-rm="rollback" title="Вернуть проект к состоянию до работы агента">↩ Откат</button>' : ""}
          ${flow.stage !== "mode" ? '<button type="button" data-rm="reset" title="Сбросить цепочку и начать заново">⟲ Заново</button>' : ""}
          <button type="button" data-rm="settings" title="Настройки агента">⚙</button>
        </span>
      </div>
    </div>` : ""}`;
  root.hidden = false;
  root.dataset.stage = flow.stage;
  // Перерисовка только при изменении: иначе опрос заменяет кнопку между нажатием и отпусканием.
  if (root.dataset.html === html) return;
  root.dataset.html = html;
  root.innerHTML = html;
  root.querySelectorAll("[data-rm]").forEach((button) => { button.onclick = () => run(button.dataset.rm); });
  root.querySelectorAll("button[data-srv]").forEach((button) => {
    button.onclick = () => {
      const act = button.dataset.srv;
      if (act === "start-all") void serverCommand("start", "all");
      else if (act === "stop-all") void serverCommand("stop", "all");
      else void serverCommand(act, button.dataset.name);
    };
  });
  const srvLog = root.querySelector("[data-srv-log]");
  if (srvLog) srvLog.ontoggle = () => { serversLogOpen = srvLog.open; };
  root.querySelector("[data-dock=toggle]").onclick = () => { setCollapsed(!collapsed()); root.dataset.html = ""; render(); };
  const goalBox = root.querySelector("#dockGoal");
  if (goalBox) goalBox.oninput = () => { goalDraft.text = goalBox.value; };
  const skip = root.querySelector("#dockSkip");
  if (skip) skip.onchange = () => { goalDraft.skipIdeas = skip.checked; };
  const answerBox = root.querySelector("#dockAnswer");
  if (answerBox) answerBox.oninput = () => { answerDraft = answerBox.value; };
  // Вариант только выбирается (текст попадает в поле и его можно дополнить); отправляет «Ответить».
  root.querySelectorAll("[data-answer]").forEach((button) => {
    button.onclick = () => { answerDraft = button.dataset.answer; render(); };
  });
  const send = root.querySelector("[data-dock=send-answer]");
  if (send) send.onclick = () => answerAgent(answerDraft);
}

/* Модальное окно само открываем только для выбора задач и отчёта: остальное — на страницах. */
function maybeAutoOpen() {
  const kind = flow && flow.stage === "select" ? "select" : flow && flow.stage === "report" ? "report" : "";
  const key = autoKey(flow);
  if (!kind || opened().has(key)) return;
  if (document.querySelector(".studio-modal") || isSwipeOpen()) return;
  if (!agentStatus(agent).connected) return;
  remember(key);
  run(kind);
}

export async function refreshRoadmap() {
  if (busy) return;
  busy = true;
  try {
    flow = await api("/api/workflow");
    try { agent = await api("/api/agent/status"); } catch (_) { agent = null; }
    await loadServers();
    await updateProgress();
    render();
    if (isSwipeOpen()) {
      if (flow.stage === "review") await refreshSwipe();
      else closeSwipe();
    }
    maybeAutoOpen();
  } catch (_error) { /* нет связи: окно не трогаем, живой индикатор покажет */ }
  finally { busy = false; }
}

export function initRoadmap(options = {}) {
  hooks = options;
  void refreshRoadmap();
  timer = setInterval(refreshRoadmap, POLL_MS);
  scheduleServers();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refreshRoadmap(); });
}

export function stopRoadmap() {
  if (timer) clearInterval(timer);
  timer = null;
  clearTimeout(serversTimer);
}

import {api} from "./api.js";
import {$, esc, toast} from "./core.js";
import {
  autoKey, autoOpen, cardsOf, defaultSelection, primaryAction, progressOf, selectionCount,
  selectionPayload, stageNote, steps,
} from "./roadmapmodel.js";
import {closeSwipe, isSwipeOpen, openSwipe, refreshSwipe} from "./swipeview.js";

/* Дорожная карта: полоса этапов под шапкой и окна, которые человек проходит по цепочке
   «синхронизация → режим → идеи → валидация → перенос → выбор → реализация → отчёт». */

const POLL_MS = 4000;
const OPENED_KEY = "studio:roadmap-opened";
let flow = null;
let timer = null;
let progress = null;
let busy = false;
let hooks = {};

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

/* --- этап 1: помощь с подключением агента */

function openConnectHelp() {
  const win = dialog("Подключите AI-агента", `
    <p>Студия сама сообщает агенту, где она запущена, — копировать токен не нужно.</p>
    <ol class="rm-steps-list">
      <li>Один раз подключите MCP-сервер студии клиенту агента:<pre>claude mcp add pacage-studio -- studio mcp</pre></li>
      <li>Один раз установите руководство в проект: <pre>studio skill --target all</pre></li>
      <li>Запустите агента в папке этого проекта и попросите: <i>«Работай со студией: вызови studio_connect и studio_workflow»</i>.</li>
    </ol>
    <p class="hint">Как только агент обратится к студии, этап «Синхронизация» отметится как выполненный. Агент общается только через MCP; чужой адрес для него закрыт.</p>`);
  win.button("Строка подключения вручную", () => { win.close(); const manual = $("agentBtn"); if (manual) manual.click(); });
  win.button("Закрыть", win.close, "primary");
}

/* --- этап 2: выбор режима */

function openModeDialog() {
  const connected = !!(flow && flow.agent && flow.agent.connected);
  const win = dialog("С чего начнём?", `
    <div class="rm-choices">
      <button type="button" class="rm-choice" data-mode="plan"><b>Планирование</b>
        <span>Агент придумает идеи и сразу разберёт их на сценарии. Вы свайпом решите, что оставить.</span></button>
      <button type="button" class="rm-choice" data-mode="implement"><b>Реализация</b>
        <span>Агент напишет код по тому, что уже запланировано на карте. Вы отметите нужные задачи.</span></button>
    </div>
    <label class="rm-task">Что нужно сделать? <span class="hint">(для планирования; необязательно)</span>
      <textarea id="rmTask" rows="3" placeholder="Например: личный сайт с портфолио, новостями и формой заявок"></textarea></label>
    ${connected ? "" : '<p class="rm-warn">Агент пока не подключён: он подхватит работу, как только появится.</p>'}`);
  win.body.querySelectorAll(".rm-choice").forEach((button) => {
    button.onclick = async () => {
      const task = win.body.querySelector("#rmTask").value;
      const mode = button.dataset.mode;
      win.close();
      remember(autoKey(flow));
      await apply(await post("/api/workflow/start", {mode, task: mode === "plan" ? task : ""}));
    };
  });
  win.button("Позже", win.close);
}

/* --- этап 5: перенос на карту */

async function backToReview() {
  const result = await post("/api/workflow/stage", {to: "review"});
  if (!result) return;
  remember(autoKey(result));
  await apply(result);
  void openSwipe({onStage: apply});
}

async function openConfirm() {
  let board = null;
  try { board = await api("/api/ideas"); } catch (error) { toast(error.message); return; }
  const accepted = cardsOf(board).filter((card) => card.decision === "accepted");
  const rejected = cardsOf(board).filter((card) => card.decision === "rejected").length;
  if (!accepted.length) {
    const win = dialog("Ничего не принято", `<p>Все карточки отклонены. Попросите агента о новых идеях или вернитесь к карточкам.</p>`);
    win.button("К карточкам", () => { win.close(); void backToReview(); });
    win.button("Попросить новые идеи", async () => { win.close(); await apply(await post("/api/workflow/stage", {to: "ideas"})); }, "primary");
    return;
  }
  const rows = accepted.slice(0, 14).map((card) =>
    `<li><b>${esc(card.title)}</b> <span class="hint">${esc(card.route || card.slug)}</span></li>`).join("");
  const more = accepted.length > 14 ? `<li class="hint">…и ещё ${accepted.length - 14}</li>` : "";
  const win = dialog("Принять все запланированные идеи и их сценарии?", `
    <p>Принято: <b>${accepted.length}</b> · отклонено: ${rejected}. После «Да» они появятся на карте как планы (латинские имена по slug); отклонённые на карту не попадут.</p>
    <ul class="rm-list">${rows}${more}</ul>
    <div id="rmConfirmError" class="rm-error" hidden></div>`);
  const no = win.button("Нет, вернуться к валидации", () => { win.close(); void backToReview(); });
  win.button("Да, перенести на карту", async (yes) => {
    yes.disabled = true;
    no.disabled = true;
    yes.textContent = "Переношу…";
    const result = await post("/api/workflow/transfer");
    const errors = (result && result.transfer && result.transfer.errors) || [];
    if (result && !errors.length) {
      win.close();
      toast(`Перенесено на карту: сценариев ${result.transfer.plans}, страниц ${result.transfer.pages}`);
      await apply(result);
      return;
    }
    yes.disabled = false;
    no.disabled = false;
    yes.textContent = "Повторить";
    if (errors.length) {
      const box = win.body.querySelector("#rmConfirmError");
      box.hidden = false;
      box.innerHTML = "<b>Не удалось перенести:</b><ul>" + errors.map((item) => `<li>${esc(item.title)} — ${esc(item.message)}</li>`).join("") + "</ul>Остальное перенесено. Исправьте карточки (вернитесь к валидации) или повторите.";
    }
    await refreshRoadmap();
  }, "primary");
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
      (p) => `<b>${esc(p.name)}</b> <small>${esc(p.route)}${p.blocks ? ` · блоков: ${p.blocks}` : ""}</small>`, selection)}
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
  const win = dialog("Отчёт агента", `
    <div class="rm-summary">${esc(summary)}</div>
    ${done.total ? `<div class="rm-progress${done.complete ? " ok" : ""}">Проверка по коду: выполнено ${done.done} из ${done.total}</div>` : ""}
    ${rows ? `<ul class="rm-results">${rows}</ul>` : ""}
    ${changes ? `<p class="hint">Агент предложил обновить описания узлов (${changes}). Проверьте и примите нужное — ничего не записано без вашего «Применить».</p>` : ""}`, "rm-report");
  if (changes) {
    win.button(`Описания от агента (${changes})…`, async () => {
      win.close();
      if (hooks.openReport) await hooks.openReport(flow.report_id);
    });
  }
  win.button("Вернуть к выбору задач", async () => { win.close(); await apply(await post("/api/workflow/stage", {to: "select"})); });
  win.button("Закрыть", win.close);
  win.button("Всё хорошо — начать заново", async () => {
    win.close();
    await apply(await post("/api/workflow/finish"));
    toast("Цикл завершён. Можно начинать заново.");
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

function run(id) {
  if (id === "mode") openModeDialog();
  else if (id === "help") openConnectHelp();
  else if (id === "review") post("/api/workflow/stage", {to: "review"}).then(apply);
  else if (id === "swipe") openSwipe({onStage: apply});
  else if (id === "confirm") openConfirm();
  else if (id === "select") openSelect();
  else if (id === "manual") confirmManual();
  else if (id === "report") openReport();
}

function stepAction(step) {
  if (!flow || step.state !== "active") return null;
  if (step.id === "sync") return "help";
  const map = {mode: "mode", review: "swipe", confirm: "confirm", select: "select", report: "report"};
  return map[step.id] || null;
}

/* ----------------------------------------------------------------- вид */

function render() {
  const root = $("roadmap");
  if (!root || !flow) return;
  const list = steps(flow);
  const action = primaryAction(flow);
  const connected = !!(flow.agent && flow.agent.connected);
  const note = flow.stage === "implement" && progress && progress.total
    ? `Агент реализует · выполнено ${progress.done} из ${progress.total}`
    : stageNote(flow);
  root.hidden = false;
  root.dataset.stage = flow.stage;
  const html = `
    <ol class="rm-track">${list.map((step, index) => `<li class="rm-step ${esc(step.state)}${stepAction(step) ? " click" : ""}" data-step="${esc(step.id)}" title="${esc(step.title)}">
        <span class="rm-dot">${step.state === "done" ? "✓" : step.state === "skipped" ? "–" : index + 1}</span><span class="rm-name">${esc(step.title)}</span></li>`).join("")}</ol>
    <div class="rm-side">
      <span class="rm-note">${esc(note)}</span>
      ${action ? `<button type="button" class="primary rm-main" data-rm="${esc(action.id)}">${esc(action.label)}</button>` : ""}
      ${flow.stage !== "mode" ? '<button type="button" class="rm-reset" data-rm="reset" title="Сбросить цепочку и начать заново">⟲ Заново</button>' : ""}
      <span class="rm-agent ${connected ? "on" : "off"}" title="${connected ? "Агент обращался к студии недавно" : "Агент не подключён"}">● агент</span>
    </div>`;
  // Перерисовка только при изменении: иначе опрос заменяет кнопку между нажатием и отпусканием.
  if (root.dataset.html === html) return;
  root.dataset.html = html;
  root.innerHTML = html;
  root.querySelectorAll("[data-rm]").forEach((button) => {
    button.onclick = () => (button.dataset.rm === "reset" ? confirmReset() : run(button.dataset.rm));
  });
  root.querySelectorAll(".rm-step.click").forEach((element) => {
    const step = list.find((item) => item.id === element.dataset.step);
    element.onclick = () => run(stepAction(step));
  });
}

function maybeAutoOpen() {
  const kind = autoOpen(flow);
  const key = autoKey(flow);
  if (!kind || opened().has(key)) return;
  if (document.querySelector(".studio-modal") || isSwipeOpen()) return;
  remember(key);
  if (kind === "swipe") openSwipe({onStage: apply});
  else run(kind);
}

export async function refreshRoadmap() {
  if (busy) return;
  busy = true;
  try {
    flow = await api("/api/workflow");
    await updateProgress();
    render();
    if (isSwipeOpen()) {
      if (flow.stage === "review") await refreshSwipe();
      else closeSwipe();
    }
    maybeAutoOpen();
  } catch (_error) { /* нет связи: полосу не трогаем, живой индикатор покажет */ }
  finally { busy = false; }
}

export function initRoadmap(options = {}) {
  hooks = options;
  void refreshRoadmap();
  timer = setInterval(refreshRoadmap, POLL_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refreshRoadmap(); });
}

export function stopRoadmap() { if (timer) clearInterval(timer); timer = null; }

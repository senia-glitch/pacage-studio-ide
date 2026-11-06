import {api} from "./api.js";
import {$, esc, modal, toast} from "./core.js";
import {copyText} from "./docdialog.js";
import {createSyncAcceptPane} from "./syncapply.js";

const MODES = {
  plan: {
    label: "Планирование",
    hint: "Агент читает проект и предлагает идеи и плановые узлы. Код и описания существующих узлов не меняет.",
  },
  implement: {
    label: "Реализация",
    hint: "Агент берёт сохранённое вами ТЗ, пишет код и проверяет результат (verify). Ничего не принимает сам: отчёт вы принимаете в окне «ТЗ».",
  },
};

export function connectionText(origin, token, mode) {
  return [
    `Studio: ${origin}`,
    `Token: ${token}`,
    `Mode: ${mode}`,
    "Заголовок запросов: X-Studio-Token. Начни с GET /api/agent — там маршруты и порядок работы.",
  ].join("\n");
}

async function briefTasks(path) {
  try {
    const brief = await api(path);
    return Array.isArray(brief.tasks) ? brief.tasks.length : 0;
  } catch (_error) {
    return null;
  }
}

function tasksWord(n) {
  const a = n % 10;
  const b = n % 100;
  if (a === 1 && b !== 11) return "задача";
  return a >= 2 && a <= 4 && (b < 10 || b >= 20) ? "задачи" : "задач";
}

function briefState(backend, frontend) {
  const part = (title, count, missing) => (count === null
    ? `${title}: ${missing}`
    : `${title}: ${count} ${tasksWord(count)}`);
  return `${part("ТЗ бэкенда", backend, "не сохранено (кнопка «ТЗ» на карте)")} · ${part("ТЗ фронтенда", frontend, "не собрано («⇄ ТЗ…» во вкладке «Фронтенд»)")}`;
}

async function openAgentDialog() {
  let info;
  try {
    info = await api("/api/agent");
  } catch (error) {
    toast(`Не удалось получить доступ для агента: ${error.message}`);
    return;
  }
  const [backend, frontend] = await Promise.all([
    briefTasks("/api/brief/last"),
    briefTasks("/api/frontend/brief/last"),
  ]);
  const tokens = info.tokens || {};
  let mode = "plan";
  const current = () => connectionText(location.origin, tokens[mode] || "", mode);
  const body = `<p class="hint">Скопируйте строку и вставьте её агенту. Токен действует, пока запущена эта студия; в файлы проекта он не записывается.</p>
    <div class="brief-controls">${Object.entries(MODES)
      .map(([key, spec]) => `<label class="hint"><input type="radio" name="agentMode" value="${esc(key)}"${key === mode ? " checked" : ""}> ${esc(spec.label)}</label>`)
      .join("")}</div>
    <p class="hint" id="agentModeHint">${esc(MODES[mode].hint)}</p>
    <p class="hint" id="agentBriefState" hidden>${esc(briefState(backend, frontend))}</p>
    <textarea id="agentConn" class="prompt-editor-text" rows="4" readonly spellcheck="false" style="min-height:0;height:104px"></textarea>
    <div class="pre-actions"><button type="button" class="primary" id="agentCopy">Скопировать для агента</button></div>`;
  modal("Подключить AI-агента", body, [{label: "Закрыть"}]);
  const overlay = document.querySelector(".studio-modal");
  if (!overlay) return;
  const area = overlay.querySelector("#agentConn");
  const hint = overlay.querySelector("#agentModeHint");
  const state = overlay.querySelector("#agentBriefState");
  const refresh = () => {
    area.value = current();
    hint.textContent = MODES[mode].hint;
    state.hidden = mode !== "implement";
  };
  overlay.querySelectorAll('input[name="agentMode"]').forEach((input) => {
    input.onchange = () => {
      mode = input.value;
      refresh();
    };
  });
  overlay.querySelector("#agentCopy").onclick = () => copyText(current(), "Строка для агента скопирована");
  refresh();
}

/* ---------- входящие отчёты агента ---------- */

const PART_TITLE = {backend: "бэкенд", frontend: "фронтенд"};
const MODE_TITLE = {plan: "планирование", implement: "реализация", full: "вручную"};
let inboxData = {reports: [], pending: 0};
let knownPending = null;

function timeText(iso) {
  return String(iso || "").replace("T", " ").slice(0, 16);
}

export async function refreshAgentReports() {
  const button = $("agentReportsBtn");
  if (!button) return;
  try {
    inboxData = await api("/api/agent/reports");
  } catch (_error) {
    return; // нет связи: бейдж не трогаем
  }
  const pending = inboxData.pending || 0;
  button.hidden = !pending;
  const count = $("agentReportsCount");
  if (count) count.textContent = String(pending);
  if (knownPending !== null && pending > knownPending) {
    toast("Агент прислал отчёт: откройте «✉» в шапке, проверьте и примите нужное");
  }
  knownPending = pending;
}

async function decide(id, decision) {
  try {
    await api("/api/agent/report/decision", "POST", {id, decision});
  } catch (error) {
    toast("Не удалось сохранить решение: " + error.message);
  }
  await refreshAgentReports();
}

function reportRow(item) {
  const c = item.counts || {};
  const tests = item.agent_report && item.agent_report.tests ? ` · тесты: ${esc(item.agent_report.tests)}` : "";
  return `<div class="inbox-row">
    <div><b>${esc(PART_TITLE[item.part] || item.part)}</b> · ${esc(timeText(item.created_at))} · режим: ${esc(MODE_TITLE[item.mode] || item.mode)}</div>
    <div class="hint">к записи: ${c.change || 0} · без изменений: ${c.same || 0} · отклонено: ${c.rejected || 0}${tests}</div>
    <div class="inbox-actions"><button type="button" class="primary" data-open="${esc(item.id)}">Открыть</button><button type="button" data-reject="${esc(item.id)}">Отклонить</button></div>
  </div>`;
}

async function openInbox() {
  await refreshAgentReports();
  const pending = (inboxData.reports || []).filter((item) => item.status === "pending");
  const list = pending.length ? pending.map(reportRow).join("") : '<p class="hint">Новых отчётов нет.</p>';
  modal("Отчёты агента", `<p class="hint">Агент присылает сюда итог работы. Пока вы не откроете отчёт и не нажмёте «Применить», ничего не записано.</p><div class="inbox-list">${list}</div>`, [{label: "Закрыть"}]);
  const overlay = document.querySelector(".studio-modal");
  if (!overlay) return;
  overlay.querySelectorAll("[data-open]").forEach((button) => { button.onclick = () => openReport(button.dataset.open); });
  overlay.querySelectorAll("[data-reject]").forEach((button) => {
    button.onclick = async () => {
      await decide(button.dataset.reject, "rejected");
      await openInbox();
    };
  });
}

async function openReport(id) {
  let record;
  try {
    record = await api("/api/agent/report?id=" + encodeURIComponent(id));
  } catch (error) {
    toast("Не удалось открыть отчёт: " + error.message);
    return;
  }
  const endpoint = record.part === "frontend" ? "/api/frontend/sync/apply" : "/api/sync/apply";
  let decided = false;
  modal(`Отчёт агента · ${PART_TITLE[record.part] || record.part} · ${timeText(record.created_at)}`, '<div id="inboxPane"></div>', [
    {label: "← К списку", action: openInbox},
    {label: "Отклонить отчёт", action: async () => { await decide(id, "rejected"); await openInbox(); }},
    {label: "Закрыть"},
  ]);
  const overlay = document.querySelector(".studio-modal");
  if (!overlay) return;
  overlay.querySelector(".modal-box")?.classList.add("brief-box");
  const pane = createSyncAcceptPane({
    endpoint,
    intro: "Это отчёт, который отправил агент. Проверьте отличия и примите нужное: записываются только отмеченные записи и только по кнопке «Применить».",
    onApplied: async () => {
      if (decided) return;
      decided = true;
      await decide(id, "accepted");
    },
  });
  overlay.querySelector("#inboxPane").appendChild(pane);
  pane.querySelector(".sync-json").value = record.raw || "";
  pane.querySelector('[data-act="check"]').click();
}

export function initAgent() {
  const button = $("agentBtn");
  if (button) button.onclick = openAgentDialog;
  const inbox = $("agentReportsBtn");
  if (inbox) inbox.onclick = openInbox;
  void refreshAgentReports();
}

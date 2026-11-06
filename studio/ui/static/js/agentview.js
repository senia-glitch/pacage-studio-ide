import {api} from "./api.js";
import {$, esc, modal, toast} from "./core.js";
import {copyText} from "./docdialog.js";

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

export function initAgent() {
  const button = $("agentBtn");
  if (button) button.onclick = openAgentDialog;
}

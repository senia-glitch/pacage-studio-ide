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
    hint: "Агент берёт ТЗ, сохранённое вами в окне «ТЗ», пишет код и проверяет результат (verify).",
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

async function openAgentDialog() {
  let info;
  try {
    info = await api("/api/agent");
  } catch (error) {
    toast(`Не удалось получить доступ для агента: ${error.message}`);
    return;
  }
  const tokens = info.tokens || {};
  let mode = "plan";
  const current = () => connectionText(location.origin, tokens[mode] || "", mode);
  const body = `<p class="hint">Скопируйте строку и вставьте её агенту. Токен действует, пока запущена эта студия; в файлы проекта он не записывается.</p>
    <div class="brief-controls">${Object.entries(MODES)
      .map(([key, spec]) => `<label class="hint"><input type="radio" name="agentMode" value="${esc(key)}"${key === mode ? " checked" : ""}> ${esc(spec.label)}</label>`)
      .join("")}</div>
    <p class="hint" id="agentModeHint">${esc(MODES[mode].hint)}</p>
    <textarea id="agentConn" class="prompt-editor-text" rows="5" readonly spellcheck="false"></textarea>`;
  modal("Подключить AI-агента", body, [
    {label: "Скопировать", cls: "primary", action: () => copyText(current(), "Строка для агента скопирована")},
    {label: "Закрыть"},
  ]);
  const overlay = document.querySelector(".studio-modal");
  if (!overlay) return;
  const area = overlay.querySelector("#agentConn");
  const hint = overlay.querySelector("#agentModeHint");
  const refresh = () => {
    area.value = current();
    hint.textContent = MODES[mode].hint;
  };
  overlay.querySelectorAll('input[name="agentMode"]').forEach((input) => {
    input.onchange = () => {
      mode = input.value;
      refresh();
    };
  });
  refresh();
}

export function initAgent() {
  const button = $("agentBtn");
  if (button) button.onclick = openAgentDialog;
}

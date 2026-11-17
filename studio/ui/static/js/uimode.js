import {api} from "./api.js";
import {toast} from "./core.js";

/* Режим интерфейса «С агентом / Вручную»: хранится в проекте (.studio/settings.json), выбирается в
   «Настройки → Режим работы» и управляет видимостью окна агента и ручных кнопок через data-ui-mode на body. */

let changeListener = null;

export function applyUiMode(mode) {
  const value = mode === "manual" ? "manual" : "agent";
  document.body.dataset.uiMode = value;
  document.querySelectorAll("[data-ui-mode]").forEach((button) => {
    button.classList.toggle("on", button.dataset.uiMode === value);
  });
  return value;
}

/* Кнопки режима создаются при отрисовке страницы «Настройки»: привязываем их после каждой отрисовки. */
export function bindUiButtons() {
  document.querySelectorAll("[data-ui-mode]").forEach((button) => {
    button.onclick = async () => {
      const mode = applyUiMode(button.dataset.uiMode);
      try { await api("/api/ui", "POST", {ui: {mode}}); }
      catch (error) { toast("Не удалось сохранить режим: " + error.message); }
      if (changeListener) changeListener(mode);
    };
  });
}

export async function initUiMode(onChange) {
  changeListener = onChange || null;
  applyUiMode("agent");
  try {
    const settings = await api("/api/ui");
    applyUiMode(settings && settings.ui && settings.ui.mode);
  } catch (_) { /* нет связи: остаётся режим с агентом по умолчанию */ }
  bindUiButtons();
}

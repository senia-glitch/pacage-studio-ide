import {api} from "./api.js";
import {toast} from "./core.js";

/* Режим интерфейса «Агент / Ручной»: хранится в проекте (.studio/settings.json),
   управляет видимостью окна агента и ручных кнопок через data-ui-mode на body. */

export function applyUiMode(mode) {
  const value = mode === "manual" ? "manual" : "agent";
  document.body.dataset.uiMode = value;
  document.querySelectorAll("[data-ui-mode]").forEach((button) => {
    button.classList.toggle("on", button.dataset.uiMode === value);
  });
  return value;
}

export async function initUiMode(onChange) {
  applyUiMode("agent");
  try {
    const settings = await api("/api/ui");
    applyUiMode(settings && settings.ui && settings.ui.mode);
  } catch (_) { /* нет связи: остаётся агент-режим по умолчанию */ }
  document.querySelectorAll("[data-ui-mode]").forEach((button) => {
    button.onclick = async () => {
      const mode = applyUiMode(button.dataset.uiMode);
      try { await api("/api/ui", "POST", {ui: {mode}}); }
      catch (error) { toast("Не удалось сохранить режим: " + error.message); }
      if (onChange) onChange(mode);
    };
  });
}

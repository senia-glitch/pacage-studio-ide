import {api} from "./api.js";
import {$, esc, toast} from "./core.js";

const PROMPT_PLACEHOLDERS = "${task}, ${endpoints}, ${scenarios}, ${models_list}, ${plans}, ${routes_package}, ${api_prefix}, ${core_discover}, ${models}";
const KINDS = {
  rules: {
    title: "Правила AI-агента",
    hint: "Вставляются в раздел «Правила работы (обязательны)» каждого ТЗ — по узлам и планам. Сверх них проект может добавлять свои правила в файле studio.rules.md (раздел «Правила этого проекта»).",
    badge: "действуют в каждом ТЗ",
  },
  decompose: {
    title: "Промпт «Собрать промпт»",
    hint: "Шаблон промпта декомпозиции в генераторе идей. Плейсхолдеры, которые заменяются данными проекта: " + PROMPT_PLACEHOLDERS + ".",
    badge: "кнопка «✦ Собрать промпт»",
  },
};

function editorHtml(kind, state) {
  const meta = KINDS[kind];
  return `<section class="prompt-editor" data-kind="${kind}">
    <div class="prompt-editor-head"><div><h3>${esc(meta.title)}</h3><span class="hint">${meta.badge}</span></div><span class="prompt-state ${state.overridden ? "over" : ""}" data-role="state">${state.overridden ? "переопределён в проекте" : "стандарт пакета"}</span></div>
    <p class="hint">${meta.hint}</p>
    <textarea data-role="text" rows="14" spellcheck="false">${esc(state.current)}</textarea>
    <div class="prompt-actions">
      <button type="button" class="primary" data-action="save">Сохранить</button>
      <button type="button" data-action="reset" ${state.overridden ? "" : "disabled"}>Вернуть стандарт</button>
      <span class="hint" data-role="saved"></span>
    </div>
  </section>`;
}

function applyState(section, state) {
  section.querySelector('[data-role="text"]').value = state.current;
  section.querySelector('[data-role="state"]').textContent = state.overridden ? "переопределён в проекте" : "стандарт пакета";
  section.querySelector('[data-role="state"]').classList.toggle("over", state.overridden);
  section.querySelector('[data-action="reset"]').disabled = !state.overridden;
}

async function renderSettings() {
  const body = $("settingsBody");
  if (!body) return;
  let data;
  try {
    data = await api("/api/settings/prompts", "GET");
  } catch (error) {
    body.innerHTML = `<p class="hint">Не удалось загрузить шаблоны: ${esc(error.message)}</p>`;
    return;
  }
  body.innerHTML = Object.keys(KINDS).map(kind => editorHtml(kind, data[kind] || {current: "", standard: "", overridden: false})).join("");
  body.querySelectorAll(".prompt-editor").forEach(section => {
    const kind = section.dataset.kind;
    section.querySelector('[data-action="save"]').onclick = async () => {
      const button = section.querySelector('[data-action="save"]');
      const saved = section.querySelector('[data-role="saved"]');
      button.disabled = true;
      if (saved) saved.textContent = "";
      try {
        const fresh = await api("/api/settings/prompts", "POST", {kind, text: section.querySelector('[data-role="text"]').value});
        applyState(section, fresh[kind]);
        if (saved) saved.textContent = "Сохранено · действует на ТЗ и промпты этого проекта";
        toast("Шаблон сохранён: " + KINDS[kind].title);
      } catch (error) {
        if (saved) saved.textContent = "Ошибка: " + error.message;
        toast("Не удалось сохранить: " + error.message);
      } finally {
        button.disabled = false;
      }
    };
    section.querySelector('[data-action="reset"]').onclick = async () => {
      try {
        const fresh = await api("/api/settings/prompts", "POST", {kind, reset: true});
        applyState(section, fresh[kind]);
        const saved = section.querySelector('[data-role="saved"]');
        if (saved) saved.textContent = "";
        toast("Возвращён стандарт пакета");
      } catch (error) {
        toast("Не удалось вернуть стандарт: " + error.message);
      }
    };
  });
}

export {renderSettings};

import {$, S, hooks} from "./core.js";
import {renderAgentSettings} from "./agentsettingsview.js";

/* «Настройки»: одно место для всего, что настраивается. Слева разделы, справа содержимое:
   Агенты (все исполнители: план, код, контроль и этап «Идея»), Проект (git, запуск, серверы),
   Промпты и Режим работы (с агентом или вручную). */

const SECTIONS = ["agents", "project", "prompts", "mode"];

function show(section) {
  const name = SECTIONS.includes(section) ? section : "agents";
  S.settingsSection = name;
  document.querySelectorAll("#settingsNav [data-sec]").forEach((button) => {
    button.classList.toggle("on", button.dataset.sec === name);
  });
  const prompts = document.querySelector('[data-sec-body="prompts"]');
  if (prompts) prompts.hidden = name !== "prompts";
  const agent = $("agentBody");
  if (agent) agent.hidden = name === "prompts";
  if (name === "prompts") {
    if (hooks.renderSettings) hooks.renderSettings();
    return;
  }
  agent.querySelectorAll("[data-sec-body]").forEach((el) => { el.hidden = el.dataset.secBody !== name; });
  void renderAgentSettings();
}

export function renderSettingsHub() {
  const nav = $("settingsNav");
  if (nav && !nav.dataset.bound) {
    nav.dataset.bound = "1";
    nav.addEventListener("click", (event) => {
      const button = event.target.closest("[data-sec]");
      if (button) show(button.dataset.sec);
    });
  }
  show(S.settingsSection);
}

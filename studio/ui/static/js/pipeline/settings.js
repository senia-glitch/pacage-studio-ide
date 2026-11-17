/* Настройки конвейера: режим участия, критерии каждых ворот, модели ролей агентов. */

import {chip, h} from "./dom.js";

const MODE_HINTS = {
  manual: "Вы решаете сами: ворота, висящее, запросы на изменение, и можете остановить любой шаг.",
  gates: "Агенты ведут этап и предлагают, вы подтверждаете на воротах. Рекомендуется.",
  autopilot: "Агенты решают сами по слоям 1–2; вы — на итоговой приёмке и когда агент застрял.",
};

const open = new Set();

export async function renderSettings(ctx, body) {
  body.append(h("p", {class: "hint"}, "Загрузка…"));
  let data;
  try {
    data = await ctx.api("/api/pipeline/settings");
  } catch (error) {
    body.replaceChildren(h("p", {class: "pl-warn"}, error.message));
    return;
  }
  body.replaceChildren(modeBlock(ctx, data), numbersBlock(ctx, data), gatesBlock(ctx, data), rolesBlock(ctx, data));
}

async function save(ctx, path, payload) {
  const answer = await ctx.act(path, payload);
  if (answer) ctx.go("settings");
  return answer;
}

function modeBlock(ctx, data) {
  return h(
    "section",
    {class: "pl-sec"},
    h("h3", null, "Режим участия"),
    h(
      "div",
      {class: "pl-modes"},
      Object.entries(data.mode_names).map(([key, name]) =>
        h(
          "label",
          {class: "pl-modeopt" + (data.mode === key ? " on" : "")},
          h("input", {type: "radio", name: "mode", checked: data.mode === key, onchange: () => save(ctx, "/api/pipeline/settings", {mode: key})}),
          h("b", null, name),
          h("span", {class: "hint"}, MODE_HINTS[key]),
        ),
      ),
    ),
    h("p", {class: "hint"}, "В любом режиме есть откат к прежней версии записи. Итоговая приёмка в автопилоте остаётся за вами."),
  );
}

function numbersBlock(ctx, data) {
  const field = (label, key, value, hint) => {
    const input = h("input", {type: "number", min: 1, value, class: "pl-num"});
    return h("div", {class: "pl-row"}, h("span", null, label), input, h("button", {onclick: () => save(ctx, "/api/pipeline/settings", {[key]: Number(input.value)})}, "Сохранить"), h("span", {class: "hint"}, hint));
  };
  return h(
    "section",
    {class: "pl-sec"},
    h("h3", null, "Пороги"),
    field("«Волны» от числа сценариев:", "wave_threshold", data.wave_threshold, "если сценариев больше — система предложит разбить этапы 3–4 на пакеты"),
    field("«Мелкая правка» — не больше записей:", "quick_fix_limit", data.quick_fix_limit, "быстрый путь без полного круга; схема БД, контракт и смысл всегда идут полным путём"),
    h("div", {class: "hint"}, `Кругов доработки подряд — не больше ${data.max_rounds}, потом агент спрашивает вас.`),
  );
}

function gatesBlock(ctx, data) {
  const sec = h("section", {class: "pl-sec"}, h("h3", null, "Критерии ворот"), h("p", {class: "hint"}, "У каждых ворот свой список. Оценка 0–10 ставится по КАЖДОМУ критерию, ворота пройдены, только если каждый не ниже своего порога (по умолчанию 8). Всё уже заполнено — править необязательно."));
  for (const gate of data.gates) {
    const isOpen = open.has(gate.key);
    const box = h("div", {class: "pl-gcrit" + (isOpen ? " open" : "")});
    box.append(h("div", {class: "pl-ghead", onclick: () => { isOpen ? open.delete(gate.key) : open.add(gate.key); ctx.go("settings"); }}, h("span", null, isOpen ? "▾" : "▸"), h("b", null, ` Этап ${gate.stage} · ${gate.title}`), h("span", {class: "hint"}, ` ${gate.criteria.filter((c) => c.enabled).length} из ${gate.criteria.length} критериев включено`)));
    if (isOpen) {
      for (const c of gate.criteria) box.append(criterionRow(ctx, gate, c));
      box.append(addRow(ctx, gate), h("button", {class: "ghost", onclick: () => save(ctx, "/api/pipeline/criterion", {gate: gate.key, action: "reset"})}, "вернуть по умолчанию"));
    }
    sec.append(box);
  }
  return sec;
}

function criterionRow(ctx, gate, c) {
  const threshold = h("input", {type: "number", min: 1, max: 10, value: c.threshold, class: "pl-num"});
  return h(
    "div",
    {class: "pl-crit" + (c.enabled ? "" : " off")},
    h("input", {type: "checkbox", checked: c.enabled, title: "включён", onchange: (e) => save(ctx, "/api/pipeline/criterion", {gate: gate.key, key: c.key, enabled: e.target.checked})}),
    h("b", null, c.title),
    h("span", {class: "hint"}, c.hint),
    h("span", {class: "spacer"}),
    "порог ",
    threshold,
    h("button", {onclick: () => save(ctx, "/api/pipeline/criterion", {gate: gate.key, key: c.key, threshold: Number(threshold.value)})}, "ок"),
    c.custom ? h("button", {class: "danger", onclick: () => save(ctx, "/api/pipeline/criterion", {gate: gate.key, action: "remove", key: c.key})}, "удалить") : chip("стандартный"),
  );
}

function addRow(ctx, gate) {
  const title = h("input", {type: "text", placeholder: "свой критерий"});
  const hint = h("input", {type: "text", placeholder: "что значит высокая оценка"});
  const threshold = h("input", {type: "number", min: 1, max: 10, value: 8, class: "pl-num"});
  return h("div", {class: "pl-crit add"}, title, hint, "порог ", threshold, h("button", {onclick: () => title.value.trim() && save(ctx, "/api/pipeline/criterion", {gate: gate.key, action: "add", title: title.value, hint: hint.value, threshold: Number(threshold.value)})}, "+ добавить"));
}

function rolesBlock(ctx, data) {
  const sec = h("section", {class: "pl-sec"}, h("h3", null, "Роли агентов"), h("p", {class: "hint"}, "Модель каждой роли выбирается здесь. Рекомендация «крутости» — подсказка, а не ограничение. Поддерживаются агенты: Claude Code Pro и OpenCode Go Plus."));
  if (data.warnings.length) sec.append(h("div", {class: "pl-warn"}, data.warnings.map((w) => h("div", null, "⚠ ", w))));
  const agentInfo = Object.fromEntries(data.agents.map((a) => [a.id, a]));
  sec.append(h("div", {class: "pl-agents"}, data.agents.map((a) => h("span", {class: "pl-agent " + (a.found ? "ok" : "no")}, a.label, a.found ? " — найден" : " — не найден (установите и войдите)"))));
  const table = h("table", {class: "pl-table roles"}, h("thead", null, h("tr", null, h("th", null, "Роль"), h("th", null, "Рекомендация"), h("th", null, "Агент"), h("th", null, "Модель"))));
  const tbody = h("tbody");
  for (const role of data.roles) {
    const agent = h("select", {onchange: (e) => save(ctx, "/api/pipeline/role", {role: role.key, agent: e.target.value, model: ""})},
      h("option", {value: "", selected: !role.agent}, "по умолчанию"),
      data.agents.map((a) => h("option", {value: a.id, selected: role.agent === a.id}, a.label)));
    const models = (agentInfo[role.agent || "claude"] || {models: []}).models || [];
    const model = h("select", {onchange: (e) => save(ctx, "/api/pipeline/role", {role: role.key, agent: role.agent, model: e.target.value})},
      h("option", {value: "", selected: !role.model}, role.agent === "opencode" || !role.recommended ? "по умолчанию" : "рекомендация: " + role.recommended),
      models.filter((m) => m.id).map((m) => h("option", {value: m.id, selected: role.model === m.id}, m.name)));
    tbody.append(h("tr", null,
      h("td", null, h("b", null, role.title), h("div", {class: "hint"}, role.where + " · " + role.description)),
      h("td", null, h("span", {class: "pl-stars"}, "★".repeat(role.tier)), " ", role.tier_name, h("div", {class: "hint"}, role.why)),
      h("td", null, agent),
      h("td", null, model)));
  }
  table.append(tbody);
  sec.append(table);
  return sec;
}

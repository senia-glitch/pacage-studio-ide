/* Запросы на изменение: правка принятого или новая фича. Решает режим участия. */

import {chip, h} from "./dom.js";

export async function renderRequests(ctx, body) {
  body.append(h("p", {class: "hint"}, "Принятое напрямую не меняется. Запрос посчитает, что он затронет: затронутое получит статус «устарело» и пройдёт ворота заново, остальное останется принятым."));
  const list = h("div", {class: "pl-reqs"}, "Загрузка…");
  const form = h("div", {class: "pl-reqform"});
  body.append(form, list);
  try {
    const data = await ctx.api("/api/pipeline/changes");
    const records = await ctx.api("/api/pipeline/bank");
    const byId = Object.fromEntries(records.records.map((r) => [r.id, r]));
    buildForm(ctx, form, records.records);
    drawList(ctx, list, data.requests.slice().reverse(), byId);
  } catch (error) {
    list.textContent = error.message;
  }
}

function buildForm(ctx, form, records) {
  const accepted = records.filter((r) => r.status === "accepted");
  const sources = records.filter((r) => ["boundary", "domain", "competitor"].includes(r.kind) && r.status !== "rejected");
  const text = h("input", {type: "text", placeholder: "что и зачем меняем"});
  const record = h("select", null, accepted.map((r) => h("option", {value: r.id, selected: r.id === ctx.requestFor}, `${r.kind_title}: ${r.title}`)));
  const title = h("input", {type: "text", placeholder: "новое название"});
  const bodyBox = h("input", {type: "text", placeholder: "новый текст (необязательно)"});
  const source = h("select", null, sources.map((r) => h("option", {value: r.id}, `${r.kind_title}: ${r.title}`)));
  const newTitle = h("input", {type: "text", placeholder: "название новой проблемы или фичи"});
  form.append(
    h("div", {class: "pl-label"}, "Новый запрос"),
    h("div", {class: "pl-row"}, h("span", null, "Описание:"), text),
    h("div", {class: "pl-row"}, h("span", null, "Изменить запись:"), record, title, bodyBox, h("button", {class: "pl-primary", disabled: !accepted.length, onclick: async () => {
      const patch = {};
      if (title.value.trim()) patch.title = title.value.trim();
      if (bodyBox.value.trim()) patch.body = bodyBox.value.trim();
      if (!Object.keys(patch).length) return ctx.toast("Укажите новое название или текст", "err");
      ctx.requestFor = null;
      await ctx.act("/api/pipeline/change", {text: text.value, patches: {[record.value]: patch}});
    }}, "Подать")),
    h("div", {class: "pl-row"}, h("span", null, "Новая проблема:"), source, newTitle, h("button", {disabled: !sources.length, onclick: async () => {
      if (!newTitle.value.trim()) return ctx.toast("Назовите новую проблему", "err");
      await ctx.act("/api/pipeline/change", {text: text.value, new: {kind: "problem", title: newTitle.value.trim(), parents: [source.value]}});
    }}, "Подать")),
  );
}

function drawList(ctx, list, requests, byId) {
  list.replaceChildren();
  if (!requests.length) {
    list.append(h("p", {class: "hint"}, "Запросов пока нет."));
    return;
  }
  for (const req of requests) {
    const impact = (req.impact || []).map((id) => (byId[id] ? byId[id].title : id));
    const targets = (req.targets || []).map((id) => (byId[id] ? byId[id].title : id));
    const item = h(
      "div",
      {class: "pl-req " + req.status},
      h("div", {class: "head"}, h("b", null, req.text), chip({open: "ждёт решения", accepted: "принят", rejected: "отклонён"}[req.status] || req.status), chip(req.size === "small" ? "мелкая правка" : "большая"), req.route ? chip(req.route === "quick" ? "быстрый путь" : "полный путь с этапа " + req.start_stage) : null, chip(`владелец: этап ${req.owner_stage}`)),
      req.type === "edit" ? h("div", {class: "hint"}, "Меняется: ", targets.join("; ")) : h("div", {class: "hint"}, "Новая запись: ", req.new.title),
      impact.length ? h("div", {class: "hint"}, `Затронет (${impact.length}): `, impact.slice(0, 8).join("; "), impact.length > 8 ? "…" : "") : h("div", {class: "hint"}, "Другие записи не затрагивает."),
      (req.conflicts || []).length ? h("div", {class: "pl-warn"}, "Повторяет существующую запись: переформулируйте или отклоните.") : null,
    );
    if (req.status === "open") {
      item.append(
        h("div", {class: "pl-actions"},
          h("button", {class: "pl-primary", onclick: () => ctx.act("/api/pipeline/change/decide", {id: req.id, decision: "accept"})}, "Принять"),
          h("button", {class: "danger", onclick: () => ctx.act("/api/pipeline/change/decide", {id: req.id, decision: "reject"})}, "Отклонить"),
          ctx.state.mode === "autopilot" ? h("span", {class: "hint"}, "В автопилоте это решает координатор — но вы можете решить и сами.") : null,
        ),
      );
    }
    if (req.status === "accepted" && (req.stale || []).length) {
      item.append(h("div", {class: "pl-actions"}, h("button", {class: "pl-primary", title: "Затронутое пройдёт свои ворота; код обновит исполнитель", onclick: () => ctx.act("/api/pipeline/change/apply", {id: req.id})}, "Перепроверить затронутое")));
    }
    list.append(item);
  }
}

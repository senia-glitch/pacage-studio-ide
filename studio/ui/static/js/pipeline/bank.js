/* Банк знаний: записи по этапам, статусы, цепочка «происходит из», правка черновика. */

import {STATUS_NAMES, chip, clear, h, statusChip} from "./dom.js";

const STAGE_NAMES = {1: "Идея", 2: "Сценарии и схема БД", 3: "Frontend", 4: "Backend", 5: "Анализ"};

const view = {stage: 1, status: "", records: [], detail: null};

export async function renderBank(ctx, body) {
  const filters = h(
    "div",
    {class: "pl-filters"},
    h("div", {class: "pl-seg"}, Object.entries(STAGE_NAMES).map(([n, name]) => h("button", {class: String(view.stage) === n ? "on" : "", onclick: () => { view.stage = Number(n); ctx.go("bank"); }}, `${n} · ${name}`))),
    h(
      "select",
      {onchange: (event) => { view.status = event.target.value; ctx.go("bank"); }},
      [["", "все статусы"], ["proposed", "черновики"], ["accepted", "принято"], ["stale", "устарело"], ["rejected", "отклонено"]].map(([value, name]) => h("option", {value, selected: view.status === value}, name)),
    ),
  );
  const list = h("div", {class: "pl-list"}, "Загрузка…");
  const detail = h("div", {class: "pl-detail"}, h("p", {class: "hint"}, "Выберите запись слева: покажем, зачем она нужна, что на ней держится и чем проверяется."));
  body.append(filters, h("div", {class: "pl-split"}, list, detail));
  try {
    const query = `?stage=${view.stage}` + (view.status ? `&status=${view.status}` : "");
    const data = await ctx.api("/api/pipeline/bank" + query);
    view.records = data.records.filter((r) => view.status === "rejected" || r.status !== "rejected" || view.status === "");
    drawList(ctx, list, detail);
    if (ctx.record) await openRecord(ctx, ctx.record, detail);
  } catch (error) {
    list.textContent = error.message;
  }
}

function drawList(ctx, list, detail) {
  clear(list);
  if (!view.records.length) {
    list.append(h("p", {class: "hint"}, "В этом разделе пока нет записей."));
    return;
  }
  const kinds = [...new Set(view.records.map((r) => r.kind_title))];
  for (const kind of kinds) {
    list.append(h("div", {class: "pl-kind"}, kind));
    for (const record of view.records.filter((r) => r.kind_title === kind)) {
      list.append(
        h(
          "div",
          {class: "pl-rec st-" + record.status + (ctx.record === record.id ? " on" : ""), onclick: () => openRecord(ctx, record.id, detail)},
          h("span", {class: "dot"}),
          h("span", {class: "t"}, record.title),
          record.constitution ? h("span", {class: "pl-star", title: "Конституция: входит в каждое ТЗ"}, "★") : null,
          record.attrs && record.attrs.below_threshold ? h("span", {class: "pl-star warn", title: "принято ниже порога"}, "!") : null,
        ),
      );
    }
  }
}

async function openRecord(ctx, id, detail) {
  ctx.record = id;
  document.querySelectorAll(".pl-rec").forEach((node) => node.classList.remove("on"));
  try {
    const data = await ctx.api("/api/pipeline/record?id=" + encodeURIComponent(id));
    drawDetail(ctx, detail, data);
  } catch (error) {
    detail.textContent = error.message;
  }
}

function attrLines(record) {
  const a = record.attrs || {};
  const lines = [];
  const add = (label, value) => value !== undefined && value !== null && value !== "" && lines.push([label, value]);
  add("версия", a.version);
  add("ответ", a.answer);
  add("важность", a.importance);
  add("приоритет", a.priority);
  add("критерий", a.check === "visual" ? "проверяется глазами" : a.check === "auto" ? "проверяется тестом" : "");
  add("тип", a.type);
  add("адрес", a.url);
  add("выбран вариант", a.chosen ? "да" : "");
  if (a.options && Array.isArray(a.options)) add("варианты ответа", a.options.join("; "));
  if (a.options === true) add("несколько способов", "да");
  if (a.assumed) add("допущение", "ответ дал агент");
  if (a.analysis) add("анализ", `${a.analysis.strengths || ""} / ${a.analysis.weaknesses || ""}`);
  return lines;
}

function drawDetail(ctx, detail, data) {
  clear(detail);
  const r = data.record;
  detail.append(h("div", {class: "pl-dhead"}, h("h3", null, r.title), statusChip(r.status), chip(r.kind_title)));
  if (r.body) detail.append(h("p", {class: "pl-quote"}, r.body));
  const lines = attrLines(r);
  if (lines.length) detail.append(h("dl", {class: "pl-attrs"}, lines.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, String(v))])));
  const attrs = r.attrs || {};
  if (attrs.diagram && attrs.diagram.steps) {
    detail.append(h("div", {class: "pl-label"}, "Диаграмма последовательности"), h("ol", {class: "pl-steps-list"}, attrs.diagram.steps.map((s) => h("li", null, `${s.from} → ${s.to}: ${s.text}`, s.tag ? chip({screen: "экран", call: "вызов", data: "данные"}[s.tag] || s.tag) : null))));
  }
  if (attrs.columns) {
    detail.append(h("div", {class: "pl-label"}, "Колонки"), h("ul", {class: "pl-cols"}, attrs.columns.map((c) => h("li", null, `${c.name}: ${c.type}`, c.key ? ` · ${c.key === "pk" ? "первичный ключ" : "внешний → " + c.ref}` : ""))));
  }
  detail.append(h("div", {class: "pl-label"}, "Зачем это здесь (цепочка до источника)"), h("div", {class: "pl-chain"}, data.chain.map((c, i) => h("span", null, i ? " ← " : "", h("a", {href: "#", onclick: (e) => { e.preventDefault(); openRecord(ctx, c.id, detail); }}, `${c.kind_title}: ${c.title}`)))));
  if (data.children.length) {
    detail.append(h("div", {class: "pl-label"}, "Что на этом держится"), h("ul", {class: "pl-cols"}, data.children.map((c) => h("li", null, h("a", {href: "#", onclick: (e) => { e.preventDefault(); openRecord(ctx, c.id, detail); }}, `${c.kind_title}: ${c.title}`), " ", statusChip(c.status)))));
  }
  detail.append(actions(ctx, detail, data));
  if (data.history.length) {
    detail.append(h("div", {class: "pl-label"}, "История"), h("div", {class: "hint"}, data.history.map((x) => `ревизия ${x.rev} · ${x.author}${x.note ? " · " + x.note : ""}`).join("  |  ")));
  }
}

function actions(ctx, detail, data) {
  const r = data.record;
  const box = h("div", {class: "pl-actions"});
  if (r.status === "proposed") {
    const title = h("input", {type: "text", value: r.title});
    const bodyBox = h("textarea", {rows: 3}, r.body || "");
    box.append(
      h("div", {class: "pl-label"}, "Править черновик"),
      title,
      bodyBox,
      h("button", {class: "pl-primary", onclick: async () => { await ctx.act("/api/pipeline/record/edit", {id: r.id, title: title.value, body: bodyBox.value}); }}, "Сохранить"),
      h("button", {class: "danger", title: "Уйдёт в архив", onclick: () => ctx.act("/api/pipeline/record/reject", {id: r.id})}, "Отклонить"),
    );
  } else if (r.status === "accepted" || r.status === "stale") {
    box.append(
      h("div", {class: "hint"}, "Принятое напрямую не меняется: подайте запрос на изменение — мы посчитаем, что он затронет."),
      h("button", {onclick: () => { ctx.requestFor = r.id; ctx.go("requests"); }}, "Запрос на изменение…"),
    );
  }
  return box;
}

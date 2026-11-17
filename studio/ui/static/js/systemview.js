import {api} from "./api.js";
import {$, S, esc, setTab} from "./core.js";

/* Обзор системы: вся система одной картой — сквозные срезы «страница → эндпоинт → сценарий →
   данные → тесты» по доменам. Данные собирает студия (GET /api/system) из карт бэкенда и фронтенда;
   здесь только показ и переходы в детальные карты. */

const KIND_NAME = {endpoint: "эндпоинт", scenario: "сценарий", model: "модель", test: "тест", page: "страница", block: "блок"};
const FILTERS = [
  ["all", "Всё"], ["planned", "К реализации"], ["issues", "Есть замечания"], ["untested", "Без тестов"],
];
let loading = false;
let filter = "all";
let last = null;

function chip(item, kind, label) {
  const planned = item.planned ? " planned" : "";
  const warn = item.issues ? " warn" : "";
  const hint = `${KIND_NAME[kind] || kind}${item.planned ? " · план" : ""}${item.issues ? ` · замечаний: ${item.issues}` : ""}`;
  return `<button type="button" class="sys-chip k-${kind}${planned}${warn}" data-open="${esc(item.id)}" data-kind="${kind}" title="${esc(hint)}">${esc(label || item.label || item.name)}</button>`;
}

const none = (text) => `<span class="sys-none">${text}</span>`;

function sliceHtml(item) {
  const pages = item.pages.length
    ? item.pages.map((p) => chip({id: p.id, label: p.kind === "block" ? `${p.page} · ${p.name}` : p.name}, p.kind)).join("")
    : none("без интерфейса");
  const scenarios = item.scenarios.length ? item.scenarios.map((s) => chip(s, "scenario")).join("") : none("без сценария");
  const data = [...item.models.map((m) => chip(m, "model")), item.io ? `<span class="sys-io" title="Обращения к базе данных из сценариев">БД: ${item.io}</span>` : ""].join("")
    || none("без данных");
  const tests = item.tests.length
    ? `<span class="sys-ok" title="${esc(item.tests.map((t) => t.label).join(", "))}">✓ тестов: ${item.tests.length}</span>`
    : '<span class="sys-warn">нет тестов</span>';
  return `<div class="sys-row${item.planned ? " planned" : ""}" data-planned="${item.planned ? 1 : 0}" data-issues="${item.issues}" data-tests="${item.tests.length}">
    <div class="sys-cell">${pages}</div><div class="sys-cell">${chip(item.endpoint, "endpoint")}</div>
    <div class="sys-cell">${scenarios}</div><div class="sys-cell">${data}</div><div class="sys-cell">${tests}</div></div>`;
}

function visible(item) {
  if (filter === "planned") return item.planned;
  if (filter === "issues") return item.issues > 0;
  if (filter === "untested") return item.tests.length === 0;
  return true;
}

function summary(s) {
  const stat = (name, value, warn) => `<div class="sys-stat${warn && value ? " warn" : ""}"><b>${value}</b><span>${name}</span></div>`;
  return `<div class="sys-summary">${stat("эндпоинтов", s.endpoints)}${stat("сценариев", s.scenarios)}${stat("моделей", s.models)}
    ${stat("страниц", s.pages)}${stat("тестов", s.tests)}${stat("к реализации", s.planned, true)}${stat("замечаний", s.diagnostics, true)}
    ${s.missing_calls ? stat("вызовов без эндпоинта", s.missing_calls, true) : ""}</div>`;
}

function ideaBar(idea) {
  if (idea.started) {
    return `<div class="sys-idea"><div><b>Идея: ${esc(idea.title || "без названия")}</b>
      <small>сценариев в документации: ${idea.scenarios}</small></div><button type="button" data-goto="idea">Открыть этап «Идея»</button></div>`;
  }
  return `<div class="sys-idea muted"><div><b>Этап «Идея» не начат</b>
    <small>Он превращает сырую идею в проверенную документацию: границы, роли, сценарии, решения. От неё отталкиваются план и ТЗ.</small></div>
    <button type="button" class="primary" data-goto="idea">Начать</button></div>`;
}

function domainHtml(domain) {
  const rows = domain.slices.filter(visible);
  if (!rows.length) return "";
  return `<section class="sys-domain"><h3>${esc(domain.name)}<small>эндпоинтов: ${rows.length}</small></h3>
    <div class="sys-grid-head"><span>Страницы</span><span>Эндпоинт</span><span>Сценарии</span><span>Данные</span><span>Тесты</span></div>
    ${rows.map(sliceHtml).join("")}</section>`;
}

function leftovers(data) {
  const parts = [];
  if (data.pages_without_backend.length && filter === "all") {
    parts.push(`<section class="sys-domain"><h3>Страницы без обращений к бэкенду<small>${data.pages_without_backend.length}</small></h3>
      <div class="sys-pad">${data.pages_without_backend.map((p) => chip({id: p.id, label: p.name, planned: p.planned}, "page")).join("")}</div></section>`);
  }
  if (data.missing_calls.length && filter !== "planned" && filter !== "untested") {
    parts.push(`<section class="sys-domain"><h3>Вызовы несуществующих эндпоинтов<small>${data.missing_calls.length}</small></h3>
      <div class="sys-pad">${data.missing_calls.map((c) => `<div class="sys-missing"><span class="sys-warn">нет эндпоинта</span> <code>${esc(c.call)}</code> — ${esc(c.page)} · ${esc(c.name)}</div>`).join("")}</div></section>`);
  }
  return parts.join("");
}

function view(data) {
  if (data.empty) {
    return `${ideaBar(data.idea)}<div class="sys-empty"><h3>В проекте пока нет кода и страниц</h3>
      <p class="hint">Карта системы появится, когда в проекте будут эндпоинты fastbase или страницы фронтенда. Начать можно с идеи: этап «Идея» подготовит документацию, а «План» превратит её в задачи для агента.</p></div>`;
  }
  const filters = `<div class="sys-filters" role="group" aria-label="Фильтр">${FILTERS.map(([id, name]) => `<button type="button" class="subtab${filter === id ? " on" : ""}" data-filter="${id}">${name}</button>`).join("")}</div>`;
  const domains = data.domains.map(domainHtml).join("");
  return `${summary(data.summary)}${ideaBar(data.idea)}${filters}${domains || '<p class="hint">Под этот фильтр ничего не подходит.</p>'}${leftovers(data)}`;
}

function bind(root) {
  root.querySelectorAll("[data-open]").forEach((el) => {
    el.onclick = () => {
      if (el.dataset.kind === "page" || el.dataset.kind === "block") { void setTab("frontend"); return; }
      S.selectedNode = el.dataset.open;
      void setTab("map");
    };
  });
  root.querySelectorAll("[data-goto]").forEach((el) => { el.onclick = () => setTab(el.dataset.goto); });
  root.querySelectorAll("[data-filter]").forEach((el) => {
    el.onclick = () => { filter = el.dataset.filter; paint(root); };
  });
}

function paint(root) {
  root.innerHTML = view(last);
  bind(root);
}

export async function renderOverview() {
  const root = $("overviewBody");
  if (!root || loading) return;
  loading = true;
  try {
    last = await api("/api/system");
    paint(root);
  } catch (error) {
    root.innerHTML = `<p class="hint">Не удалось построить обзор: ${esc(error.message)}</p>`;
  } finally {
    loading = false;
  }
}

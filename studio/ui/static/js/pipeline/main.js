/* Конвейер: главный экран студии v3. Путь из пяти этапов, банк знаний, запросы, настройки. */

import {api} from "../api.js";
import {clear, h} from "./dom.js";
import {renderBank} from "./bank.js";
import {renderMapView} from "./maps.js";
import {renderPath} from "./path.js";
import {renderRequests} from "./requests.js";
import {renderSettings} from "./settings.js";

const VIEWS = [
  ["path", "Путь"],
  ["map", "Карта системы"],
  ["bank", "Банк знаний"],
  ["requests", "Запросы на изменение"],
  ["settings", "Настройки"],
];

const ctx = {
  host: null,
  state: null,
  view: "path",
  timer: null,
  gate: null, // открытые ворота на экране «Путь»
  record: null, // открытая запись в банке
  toastTimer: null,
};

ctx.api = api;

/* Экраны подгружают данные асинхронно: пока данных нет, страница короткая и прокрутка сбрасывается
   вверх. Поэтому каждая асинхронная загрузка регистрируется здесь, а draw() ждёт их все и только
   потом возвращает прокрутку (и пока ждёт, держит прежнюю высоту). */
ctx.pending = [];
ctx.track = (promise) => {
  ctx.pending.push(promise);
  return promise;
};

const SCROLLERS = ".pl-body, .pl-list, .pl-detail, .pl-mapwrap, .pl-gatepanel";

function scrollSnapshot(host) {
  return [...host.querySelectorAll(SCROLLERS)].map((el) => [el.className, el.scrollTop]);
}

function restoreScroll(host, snapshot) {
  const used = new Set();
  for (const [name, top] of snapshot) {
    const el = [...host.querySelectorAll(SCROLLERS)].find((item, i) => item.className === name && !used.has(item));
    if (el) {
      used.add(el);
      el.scrollTop = top;
    }
  }
}

ctx.toast = (message, kind = "") => {
  const node = ctx.host && ctx.host.querySelector(".pl-toast");
  if (!node) return;
  node.textContent = message;
  node.className = "pl-toast on " + kind;
  clearTimeout(ctx.toastTimer);
  ctx.toastTimer = setTimeout(() => (node.className = "pl-toast"), kind === "err" ? 9000 : 3500);
};

ctx.act = async (path, body) => {
  try {
    const answer = await api(path, "POST", body || {});
    await ctx.refresh();
    return answer;
  } catch (error) {
    ctx.toast(error.message || "Не получилось", "err");
    await ctx.refresh();
    return null;
  }
};

ctx.go = (view, extra) => {
  ctx.view = view;
  if (extra && extra.record !== undefined) ctx.record = extra.record;
  draw();
};

ctx.refresh = async () => {
  const before = ctx.state && ctx.state.job && ctx.state.job.running;
  try {
    ctx.state = await api("/api/pipeline/state");
  } catch (error) {
    ctx.toast(error.message, "err");
    return;
  }
  const running = ctx.state.job && ctx.state.job.running;
  if (before && !running && ctx.state.job) {
    ctx.openGateOnce = true; // работа закончилась: раскрыть плашку ворот с замечаниями
    ctx.toast(ctx.state.job.error ? "Ошибка: " + ctx.state.job.error : "Готово: " + (ctx.state.job.result || ""), ctx.state.job.error ? "err" : "ok");
  }
  draw();
  schedulePoll();
};

function schedulePoll() {
  clearTimeout(ctx.timer);
  if (ctx.state && ctx.state.job && ctx.state.job.running) ctx.timer = setTimeout(poll, 1500);
}

async function poll() {
  if (!ctx.host || !ctx.host.isConnected) return;
  try {
    const next = await api("/api/pipeline/state");
    const wasRunning = ctx.state && ctx.state.job && ctx.state.job.running;
    ctx.state = next;
    if (wasRunning && !(next.job && next.job.running)) {
      await ctx.refresh();
      return;
    }
    drawJob();
  } catch (_error) {
    /* связь мигнула — попробуем ещё раз */
  }
  schedulePoll();
}

function jobBar() {
  const job = ctx.state && ctx.state.job;
  const node = h("div", {class: "pl-job"});
  if (!job) return node;
  if (job.running) {
    node.className = "pl-job on";
    node.append(h("span", {class: "pl-spin"}), "Работает: ", job.label, "…");
  } else if (job.error) {
    node.className = "pl-job err";
    node.append("Не получилось (", job.label, "): ", job.error);
  } else {
    node.className = "pl-job ok";
    node.append("Готово (", job.label, "): ", job.result || "");
  }
  return node;
}

function drawJob() {
  const old = ctx.host && ctx.host.querySelector(".pl-job");
  if (old) old.replaceWith(jobBar());
}

function draw() {
  const host = ctx.host;
  if (!host) return;
  const oldBody = host.querySelector(".pl-body");
  const snapshot = scrollSnapshot(host);
  const height = oldBody ? oldBody.scrollHeight : 0;
  const focused = host.contains(document.activeElement) ? document.activeElement.dataset.focus || "" : "";
  clear(host);
  const state = ctx.state || {};
  const bar = h(
    "div",
    {class: "pl-bar"},
    h("div", {class: "pl-title"}, h("b", null, "Конвейер"), h("span", {class: "hint"}, " от идеи до работающего ПО")),
    h(
      "nav",
      {class: "pl-views", role: "tablist"},
      VIEWS.map(([key, name]) =>
        h("button", {class: "pl-view" + (ctx.view === key ? " on" : ""), role: "tab", onclick: () => ctx.go(key)}, name),
      ),
    ),
    h("span", {class: "spacer"}),
    state.started ? modeSelect(state) : null,
  );
  const body = h("div", {class: "pl-body"});
  host.append(bar, jobBar(), h("div", {class: "pl-toast"}), body);
  body.style.minHeight = height ? height + "px" : "";
  if (ctx.view === "path") renderPath(ctx, body);
  else if (ctx.view === "map") renderMapView(ctx, body);
  else if (ctx.view === "bank") ctx.track(renderBank(ctx, body));
  else if (ctx.view === "requests") ctx.track(renderRequests(ctx, body));
  else ctx.track(renderSettings(ctx, body));
  restoreScroll(host, snapshot);
  settle(host, body, snapshot, focused);
}

async function settle(host, body, snapshot, focused) {
  while (ctx.pending.length) await Promise.allSettled(ctx.pending.splice(0));
  if (!host.contains(body)) return; // за это время экран уже перерисован
  body.style.minHeight = "";
  restoreScroll(host, snapshot);
  if (focused) {
    const again = host.querySelector(`[data-focus="${focused}"]`);
    if (again) again.focus({preventScroll: true});
  }
}

function modeSelect(state) {
  const select = h(
    "select",
    {
      class: "pl-mode",
      "data-focus": "bar-mode",
      title: "Кто принимает решения: вы, агенты вместе с вами или агенты сами",
      onchange: (event) => ctx.act("/api/pipeline/settings", {mode: event.target.value}),
    },
    Object.entries(state.mode_names || {}).map(([key, name]) => h("option", {value: key, selected: key === state.mode}, name)),
  );
  return h("label", {class: "pl-modebox"}, "Режим участия: ", select);
}

/* Открыть экран конвейера на нужной вкладке (например, «settings» — из «Настройки»). */
export function showView(view) {
  ctx.view = view;
  if (ctx.host && ctx.state) draw();
}

export async function renderPipeline(host) {
  ctx.host = host;
  await ctx.refresh();
}

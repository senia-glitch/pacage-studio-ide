/* Карты: колонки по цепочке «происходит из», узлы цветом статуса, связи линиями. */

import {clear, h} from "./dom.js";

const SVG = "http://www.w3.org/2000/svg";
const COLORS = {
  ok: ["#25382a", "#4f9a5a"],
  plan: ["#2e2f33", "#8a8b93"],
  stale: ["#4a3c20", "#e0a030"],
  gap: ["#4a2a2a", "#c0504d"],
  orphan: ["#3a3a3a", "#888"],
};
const STATE_NAMES = {
  ok: "принято",
  plan: "план (черновик)",
  stale: "устарело",
  gap: "разрыв",
  orphan: "из кода без описания",
};

export const MAP_KINDS = [
  ["trace", "Карта системы"],
  ["er", "ER-диаграмма"],
  ["front", "Фронтенд"],
  ["back", "Бэкенд"],
];

const el = (name, attrs, text) => {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
};

export async function renderMap(ctx, box, kind) {
  clear(box);
  box.append(h("p", {class: "hint"}, "Загрузка карты…"));
  let data;
  try {
    data = await ctx.api("/api/pipeline/map?kind=" + encodeURIComponent(kind));
  } catch (error) {
    box.replaceChildren(h("p", {class: "pl-warn"}, error.message));
    return;
  }
  box.replaceChildren(legend(data), draw(ctx, data));
}

function legend(data) {
  return h(
    "div",
    {class: "pl-maplegend"},
    h("b", null, data.title),
    Object.keys(COLORS).map((state) =>
      h("span", {class: "item"}, h("i", {style: `background:${COLORS[state][0]};border-color:${COLORS[state][1]}`}), `${STATE_NAMES[state]}${data.counts[state] ? " · " + data.counts[state] : ""}`),
    ),
    h("span", {class: "hint"}, "пунктир — план, сплошная — принято; клик по узлу открывает запись"),
  );
}

function draw(ctx, data) {
  const colW = data.edges.some((e) => e.label) ? 250 : 190; // подписи связей требуют места
  const nodeW = 164;
  const nodeH = 34;
  const gapY = 12;
  const cols = Math.max(data.columns.length, 1);
  const perCol = {};
  const pos = {};
  for (const node of data.nodes) {
    const col = Math.min(node.col, cols - 1);
    const row = (perCol[col] = (perCol[col] || 0) + 1) - 1;
    pos[node.id] = {x: 16 + col * colW, y: 44 + row * (nodeH + gapY), node};
  }
  const rows = Math.max(...Object.values(perCol), 1);
  const width = 32 + cols * colW;
  const height = 70 + rows * (nodeH + gapY) + 20;
  const svg = el("svg", {class: "pl-mapsvg", width, height, viewBox: `0 0 ${width} ${height}`});
  data.columns.forEach((name, i) => svg.append(el("text", {x: 16 + i * colW, y: 24, class: "colhead"}, name.length > 20 ? name.slice(0, 19) + "…" : name)));
  for (const edge of data.edges) {
    const a = pos[edge.from];
    const b = pos[edge.to];
    if (!a || !b) continue;
    const y1 = a.y + nodeH / 2;
    const y2 = b.y + nodeH / 2;
    let x1 = a.x + nodeW;
    let x2 = b.x;
    let d;
    if (b.x > a.x) {
      const mid = (x1 + x2) / 2;
      d = `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
    } else if (b.x < a.x) {
      x1 = a.x;
      x2 = b.x + nodeW;
      const mid = (x1 + x2) / 2;
      d = `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
    } else {
      x2 = b.x + nodeW;
      d = `M${x1},${y1} C${x1 + 56},${y1} ${x2 + 56},${y2} ${x2},${y2}`; // та же колонка: дуга справа
    }
    const mid = b.x === a.x ? x1 + 44 : (x1 + x2) / 2;
    const dashed = a.node.state === "plan" || b.node.state === "plan";
    svg.append(el("path", {d, class: "edge" + (dashed ? " dashed" : "")}));
    if (edge.label) svg.append(el("text", {x: mid, y: (y1 + y2) / 2 - 3, class: "elabel"}, edge.label));
  }
  for (const {x, y, node} of Object.values(pos)) {
    const [fill, stroke] = COLORS[node.state] || COLORS.plan;
    const group = el("g", {class: "node", transform: `translate(${x},${y})`});
    group.append(el("rect", {width: nodeW, height: nodeH, rx: 8, fill, stroke, "stroke-width": node.constitution ? 2.4 : 1.4, "stroke-dasharray": node.state === "plan" ? "5 3" : ""}));
    const label = node.label.length > 24 ? node.label.slice(0, 23) + "…" : node.label;
    group.append(el("text", {x: 10, y: nodeH / 2 + 4}, (node.constitution ? "★ " : "") + label));
    group.append(el("title", {}, `${node.label} — ${STATE_NAMES[node.state] || node.state}`));
    group.addEventListener("click", () => ctx.go("bank", {record: node.id}));
    svg.append(group);
  }
  return h("div", {class: "pl-mapwrap"}, svg);
}

export function renderMapView(ctx, body) {
  const holder = h("div", {class: "pl-map"});
  const kind = ctx.mapKind || "trace";
  const tabs = h(
    "div",
    {class: "pl-seg"},
    MAP_KINDS.map(([key, name]) => h("button", {class: kind === key ? "on" : "", onclick: () => { ctx.mapKind = key; ctx.go("map"); }}, name)),
  );
  body.append(h("p", {class: "hint"}, "Карты строятся сами из банка знаний: план — из черновиков, факт — из принятого, цвет — статус. Разрывы подсвечены красным."), tabs, holder);
  renderMap(ctx, holder, kind);
}

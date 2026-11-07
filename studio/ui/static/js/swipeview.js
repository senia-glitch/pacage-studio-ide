import {api} from "./api.js";
import {esc, toast} from "./core.js";
import {cardsOf, swipeDecision, swipeProgress, tally} from "./roadmapmodel.js";

/* Валидация карточек свайпом: вверх — принять, вниз — отклонить.
   Решения сразу уходят на сервер (доска идей в .studio/ideas.json), закрытие окна ничего не теряет. */

let overlay = null;
let queue = [];
let history = [];
let stats = {accepted: 0, rejected: 0, total: 0};
let handlers = {};
let busy = false;

export function isSwipeOpen() { return !!overlay; }

export async function openSwipe(options = {}) {
  handlers = options;
  if (overlay) { await refreshSwipe(); return; }
  history = [];
  overlay = document.createElement("div");
  overlay.className = "swipe-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-label", "Валидация карточек");
  document.body.appendChild(overlay);
  document.addEventListener("keydown", onKey, true);
  await load(true);
  render();
}

export function closeSwipe() {
  if (!overlay) return;
  document.removeEventListener("keydown", onKey, true);
  overlay.remove();
  overlay = null;
  if (handlers.onClose) handlers.onClose();
}

/* Агент мог прислать ещё карточки, пока человек листает: добавляем их в конец очереди. */
export async function refreshSwipe() {
  if (!overlay || busy) return;
  const before = signature();
  await load(false);
  if (signature() !== before) render(); // лишняя перерисовка сбивает жест и клик по кнопке
}

function signature() {
  return `${queue.map((card) => card.id).join(",")}|${stats.accepted}|${stats.rejected}`;
}

async function load(reset) {
  let board;
  try { board = await api("/api/ideas"); }
  catch (error) { toast("Не удалось загрузить карточки: " + error.message); return; }
  const all = cardsOf(board);
  const known = new Set(queue.map((card) => card.id));
  const local = new Set(history.map((entry) => entry.card.id));
  if (reset) queue = [];
  for (const card of all) {
    if (card.decision !== "pending" || local.has(card.id)) continue;
    if (reset || !known.has(card.id)) queue.push(card);
  }
  queue = queue.filter((card) => all.some((item) => item.id === card.id && item.decision === "pending"));
  const counts = tally(all);
  stats = {accepted: counts.accepted, rejected: counts.rejected, total: counts.total};
}

/* ---------------------------------------------------------------- отрисовка */

function list(items, cls) {
  return items.length ? `<ul class="${cls}">${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : "";
}

function cardHtml(card, depth) {
  const blocks = card.blocks.length
    ? `<div class="sw-label">БЛОКИ</div>${list(card.blocks.map((b) => b.name + (b.description ? " — " + b.description : "")), "sw-list")}`
    : "";
  const calls = card.calls.length ? `<div class="sw-label">ВЫЗЫВАЕТ</div>${list(card.calls, "sw-list sw-mono")}` : "";
  const acceptance = card.acceptance.length ? `<div class="sw-label">КРИТЕРИИ ПРИЁМКИ</div>${list(card.acceptance, "sw-list")}` : "";
  const meta = [card.domain ? `домен: ${esc(card.domain)}` : "", ...card.extras.map(esc)].filter(Boolean).join(" · ");
  return `<article class="sw-card sw-${esc(card.kind)}" data-depth="${depth}" data-card="${esc(card.id)}">
    <div class="sw-kicker"><span class="sw-kind">${esc(card.kindLabel)}</span><span class="sw-idea">${esc(card.idea)}</span></div>
    <h2 class="sw-title">${esc(card.title)}</h2>
    ${card.route ? `<div class="sw-route">${esc(card.route)}</div>` : ""}
    ${card.slug ? `<div class="sw-slug">${esc(card.slug)}</div>` : ""}
    ${meta ? `<div class="sw-meta">${meta}</div>` : ""}
    <p class="sw-desc">${esc(card.description) || '<span class="hint">без описания</span>'}</p>
    ${acceptance}${calls}${blocks}
  </article>`;
}

function render() {
  if (!overlay) return;
  if (!queue.length) { renderDone(); return; }
  const position = stats.accepted + stats.rejected + 1;
  const shown = queue.slice(0, 3);
  overlay.innerHTML = `
    <div class="sw-top">
      <div class="sw-count">Карточка ${position} из ${stats.accepted + stats.rejected + queue.length}</div>
      <div class="sw-tally"><span class="ok">▲ принято ${stats.accepted}</span><span class="no">▼ отклонено ${stats.rejected}</span></div>
      <button type="button" class="sw-close" data-sw="close" title="Закрыть (Esc): решения сохранены">Закрыть</button>
    </div>
    <div class="sw-stage">
      <div class="sw-edge up"><span>▲ принять</span></div>
      <div class="sw-deck">${shown.map((card, index) => cardHtml(card, index)).reverse().join("")}</div>
      <div class="sw-edge down"><span>▼ отклонить</span></div>
    </div>
    <div class="sw-bar">
      <button type="button" class="sw-no" data-sw="rejected">▼ Отклонить</button>
      <button type="button" data-sw="undo"${history.length ? "" : " disabled"} title="Вернуть прошлую карточку (Z)">↩ Назад</button>
      <button type="button" class="sw-yes" data-sw="accepted">▲ Принять</button>
    </div>
    <div class="sw-hint">Потяните карточку мышью вверх, чтобы принять, или вниз, чтобы отклонить · клавиши ↑ ↓ и Z</div>`;
  overlay.querySelectorAll("[data-sw]").forEach((button) => { button.onclick = () => action(button.dataset.sw); });
  const top = overlay.querySelector('.sw-card[data-depth="0"]');
  if (top) bindDrag(top);
}

function renderDone() {
  const accepted = stats.accepted;
  overlay.innerHTML = `
    <div class="sw-top"><div class="sw-count">Валидация завершена</div><div class="sw-tally"><span class="ok">▲ принято ${accepted}</span><span class="no">▼ отклонено ${stats.rejected}</span></div><button type="button" class="sw-close" data-sw="close">Закрыть</button></div>
    <div class="sw-done">
      <h2>${accepted ? "Все карточки разобраны" : "Ничего не принято"}</h2>
      <p>${accepted
        ? `Принято: ${accepted} · отклонено: ${stats.rejected}. Дальше — перенос принятого на карту.`
        : "Все карточки отклонены. Попросите у агента другие идеи или начните цепочку заново."}</p>
      <div class="sw-done-actions">
        <button type="button" data-sw="undo"${history.length ? "" : " disabled"}>↩ Вернуть последнюю</button>
        <button type="button" data-sw="more">Попросить ещё идей</button>
        ${accepted ? '<button type="button" class="primary" data-sw="confirm">Далее: перенос на карту</button>' : '<button type="button" class="primary" data-sw="restart">Начать заново</button>'}
      </div>
    </div>`;
  overlay.querySelectorAll("[data-sw]").forEach((button) => { button.onclick = () => action(button.dataset.sw); });
}

/* ------------------------------------------------------------------ действия */

async function stage(to) {
  try { return await api("/api/workflow/stage", "POST", {to}); }
  catch (error) { toast(error.message); return null; }
}

function action(name) {
  if (name === "close") closeSwipe();
  else if (name === "accepted" || name === "rejected") flyAway(name);
  else if (name === "undo") undo();
  else if (name === "confirm") finish("confirm");
  else if (name === "more") finish("ideas");
  else if (name === "restart") finish("mode");
}

async function finish(to) {
  const flow = await stage(to);
  if (!flow) return;
  closeSwipe();
  if (handlers.onStage) handlers.onStage(flow);
}

async function commit(decision) {
  const card = queue[0];
  if (!card || busy) return;
  busy = true;
  queue.shift();
  history.push({card, decision});
  stats[decision] += 1;
  try {
    await api("/api/workflow/decide", "POST", {id: card.id, decision});
  } catch (error) {
    history.pop();
    queue.unshift(card);
    stats[decision] -= 1;
    toast("Решение не сохранено: " + error.message);
  } finally { busy = false; }
  render();
}

async function undo() {
  const last = history[history.length - 1];
  if (!last || busy) return;
  busy = true;
  try {
    await api("/api/workflow/decide", "POST", {id: last.card.id, decision: "pending"});
    history.pop();
    queue.unshift(last.card);
    stats[last.decision] -= 1;
  } catch (error) { toast("Не удалось вернуть карточку: " + error.message); }
  finally { busy = false; }
  render();
}

function flyAway(decision, element) {
  const card = element || (overlay && overlay.querySelector('.sw-card[data-depth="0"]'));
  if (!card || busy || !queue.length) return;
  card.classList.remove("dragging");
  card.classList.add("flying");
  const sign = decision === "accepted" ? -1 : 1;
  card.style.transform = `translate(0, ${sign * 125}vh) rotate(${sign * 4}deg)`;
  card.style.opacity = "0";
  setTimeout(() => { if (overlay) commit(decision); }, 200);
}

function edges(progress) {
  if (!overlay) return;
  const up = overlay.querySelector(".sw-edge.up");
  const down = overlay.querySelector(".sw-edge.down");
  if (up) up.style.opacity = progress.side === "accepted" ? String(0.25 + 0.75 * progress.strength) : "0.25";
  if (down) down.style.opacity = progress.side === "rejected" ? String(0.25 + 0.75 * progress.strength) : "0.25";
}

function bindDrag(element) {
  let active = false;
  let startX = 0;
  let startY = 0;
  let dx = 0;
  let dy = 0;
  let lastY = 0;
  let lastT = 0;
  let velocity = 0;
  element.addEventListener("pointerdown", (event) => {
    if (busy || event.button > 0 || event.target.closest("button, a, input, textarea")) return;
    active = true;
    startX = event.clientX;
    startY = event.clientY;
    lastY = event.clientY;
    lastT = performance.now();
    velocity = 0;
    element.setPointerCapture(event.pointerId);
    element.classList.add("dragging");
  });
  element.addEventListener("pointermove", (event) => {
    if (!active) return;
    dx = event.clientX - startX;
    dy = event.clientY - startY;
    const now = performance.now();
    if (now > lastT) { velocity = (event.clientY - lastY) / (now - lastT); lastY = event.clientY; lastT = now; }
    element.style.transform = `translate(${dx * 0.3}px, ${dy}px) rotate(${dx * 0.03}deg)`;
    const progress = swipeProgress(dy);
    element.dataset.side = progress.side;
    element.style.setProperty("--sw-strength", String(progress.strength));
    edges(progress);
  });
  const end = (event) => {
    if (!active) return;
    active = false;
    if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
    const decision = event.type === "pointercancel" ? null : swipeDecision(dy, velocity);
    edges({side: "", strength: 0});
    if (decision) { flyAway(decision, element); return; }
    element.classList.remove("dragging");
    element.dataset.side = "";
    element.style.transform = "";
    dx = 0;
    dy = 0;
  };
  element.addEventListener("pointerup", end);
  element.addEventListener("pointercancel", end);
}

function onKey(event) {
  if (!overlay) return;
  const tag = (event.target && event.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (document.querySelector(".studio-modal")) return;
  const key = event.key;
  let handled = true;
  if (key === "ArrowUp" || key === "Enter") flyAway("accepted");
  else if (key === "ArrowDown" || key === "Delete" || key === "Backspace") flyAway("rejected");
  else if (key.toLowerCase() === "z" || key.toLowerCase() === "я") undo();
  else if (key === "Escape") closeSwipe();
  else handled = false;
  if (handled) { event.preventDefault(); event.stopPropagation(); }
}

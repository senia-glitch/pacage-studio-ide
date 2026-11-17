import {domRoot} from "./dom.js";
/* Общее состояние интерфейса, запросы к оболочке и мелкие помощники. */

export {esc} from "./canvas.js";
import {esc} from "./canvas.js";

export const S = {
  boot: null,          // шаги, настройки, проекты
  project: null,       // состояние проекта: версия, текущая работа агентов
  graph: null,         // карточки и связи
  bank: [],            // записи банка знаний
  step: 0,             // шаг цепочки, открытый слева
};

export const $ = (id) => domRoot().getElementById(id);

const bus = new EventTarget();
export const on = (name, fn) => bus.addEventListener(name, (event) => fn(event.detail));
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, {detail}));

function studioToken() {
  const meta = document.querySelector('meta[name="studio-token"]');
  return meta ? meta.getAttribute("content") || "" : "";
}

/* Запросы к студии: адреса /api/... этого интерфейса живут под /api/idea/. */
export async function api(path, data) {
  const url = path.replace(/^\/api\//, "/api/idea/");
  const headers = {"X-Studio-Token": studioToken()};
  const init = data === undefined
    ? {headers}
    : {method: "POST", headers: {...headers, "Content-Type": "application/json"}, body: JSON.stringify(data)};
  let response, body;
  try {
    response = await fetch(url, init);
    body = await response.json();
  } catch (error) {
    throw new Error("Студия не отвечает. Проверьте, что команда studio ещё запущена.");
  }
  if (!response.ok) {
    const info = body && body.error ? body.error : {};
    const failure = new Error(info.message || "Ошибка запроса");
    failure.code = info.code || `http_${response.status}`;
    throw failure;
  }
  return body;
}

let toastTimer = 0;
export function toast(text, ms = 4500) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

/* Действие с показом ошибки: всё, что нажимает человек, идёт через эту обёртку. */
export async function act(fn) {
  try {
    return await fn();
  } catch (error) {
    toast(error.message || String(error));
    return undefined;
  }
}

export const cards = (kind) => (S.graph?.cards || []).filter((c) => c.kind === kind).sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));

/* Карточки данного вида, лежащие непосредственно под родителем (связь «детализирует»). */
export function childrenOf(parentId, kind) {
  const ids = new Set((S.graph?.edges || []).filter((e) => e.to === parentId && e.kind === "детализирует").map((e) => e.from));
  return cards(kind).filter((c) => ids.has(c.id));
}

/* Роли сценария: по связям «участвует». */
export function rolesOf(scenarioId) {
  const ids = new Set((S.graph?.edges || []).filter((e) => e.from === scenarioId && e.kind === "участвует").map((e) => e.to));
  return cards("роль").filter((c) => ids.has(c.id));
}

export const records = (type) => S.bank.filter((r) => r.type === type);

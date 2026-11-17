/* Небольшие помощники: создать элемент одной строкой и не вставлять чужой текст как HTML. */

export function h(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else if (key === "checked" || key === "disabled" || key === "selected") node[key] = !!value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

export const clear = (node) => {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
};

export const STATUS_NAMES = {
  proposed: "черновик",
  accepted: "принято",
  stale: "устарело",
  rejected: "отклонено",
};

export const GATE_STATE_NAMES = {
  none: "ещё не запускали",
  rating: "идёт оценка",
  ready: "ждёт решения",
  returned: "вернули на доработку",
  passed: "принято",
};

export function chip(text, cls) {
  return h("span", {class: "pl-chip" + (cls ? " " + cls : "")}, text);
}

export function statusChip(status) {
  return chip(STATUS_NAMES[status] || status, "st-" + status);
}

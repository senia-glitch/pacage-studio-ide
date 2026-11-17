export const $ = (id) => document.getElementById(id);

export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function toast(message) {
  const element = $("toast");
  if (!element) return;
  element.textContent = message;
  element.style.display = "block";
  clearTimeout(element._h);
  element._h = setTimeout(() => {
    element.style.display = "none";
  }, 3000);
}

/* Перерисовка панели не должна сбрасывать прокрутку: запоминаем её у самой панели и у вложенных
   прокручиваемых блоков (селекторы), выполняем перерисовку и возвращаем. ":self" — сама панель. */
export function keepScroll(root, selectors, redraw) {
  if (!root) return redraw();
  const find = (sel) => (sel === ":self" ? root : root.querySelector(sel));
  const saved = selectors.map((sel) => [sel, (find(sel) || {}).scrollTop || 0]);
  const result = redraw();
  for (const [sel, top] of saved) {
    const el = find(sel);
    if (el && top) el.scrollTop = top;
  }
  return result;
}

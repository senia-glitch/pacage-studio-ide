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

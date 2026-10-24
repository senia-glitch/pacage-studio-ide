import {offsetInStage} from "./edges.js";
import {$} from "./util.js";

export function bindViewport(canvasId, view, apply) {
  const canvas = $(canvasId);
  if (!canvas) return;
  let drag = null;
  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const next = Math.min(2.5, Math.max(0.2, view.k * (event.deltaY < 0 ? 1.12 : 0.89)));
      const scale = next / view.k;
      view.x = px - (px - view.x) * scale;
      view.y = py - (py - view.y) * scale;
      view.k = next;
      apply();
    },
    {passive: false},
  );
  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    if (event.target.closest && event.target.closest(".card")) return;
    drag = {x: event.clientX, y: event.clientY, vx: view.x, vy: view.y};
    canvas.classList.add("grabbing");
  });
  window.addEventListener("mousemove", (event) => {
    if (!drag) return;
    view.x = drag.vx + event.clientX - drag.x;
    view.y = drag.vy + event.clientY - drag.y;
    apply();
  });
  window.addEventListener("mouseup", () => {
    drag = null;
    canvas.classList.remove("grabbing");
  });
  canvas.addEventListener("dblclick", () => fitView(canvas, view, apply));
}

export function applyTransform(viewId, view, onCreateView) {
  const element = $(viewId);
  if (element) element.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
  const label = $("zoomLabel");
  if (label) label.textContent = `${Math.round(view.k * 100)}%`;
  if (viewId === "createView" && onCreateView) onCreateView();
}

export function fitView(canvas, view, apply) {
  const stage = canvas.querySelector(".stage");
  if (!stage) return;
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  const w = stage.offsetWidth || 1;
  const h = stage.offsetHeight || 1;
  const k = Math.min(2, Math.max(0.2, Math.min((cw - 60) / w, (ch - 60) / h)));
  view.k = k;
  view.x = (cw - w * k) / 2;
  view.y = (ch - h * k) / 2;
  apply();
}

export function revealElement(canvas, view, element, apply) {
  const stage = canvas.querySelector(".stage");
  if (!stage || !element) return;
  const position = offsetInStage(element, stage);
  const w = element.offsetWidth || 1;
  const h = element.offsetHeight || 1;
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  const margin = 40;
  const left = -view.x / (view.k || 1);
  const top = -view.y / (view.k || 1);
  const right = left + cw / (view.k || 1);
  const bottom = top + ch / (view.k || 1);
  if (
    position.x >= left + margin &&
    position.y >= top + margin &&
    position.x + w <= right - margin &&
    position.y + h <= bottom - margin
  ) {
    return;
  }
  view.x = cw / 2 - (position.x + w / 2) * (view.k || 1);
  view.y = ch / 2 - (position.y + h / 2) * (view.k || 1);
  apply();
}

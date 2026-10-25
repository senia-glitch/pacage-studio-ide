function bindDrag(element, handle, view, onMove, onEnd, hooks = {}) {
  if (!handle) return;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (element.classList.contains("dragging")) return;
    if (event.target.closest("button,input,select,textarea,.no-drag")) return;
    event.preventDefault();
    event.stopPropagation();
    try {
      handle.setPointerCapture(event.pointerId);
    } catch (_error) {
      // pointer capture не обязателен
    }
    const sx = event.clientX;
    const sy = event.clientY;
    const startX = Number.parseFloat(element.style.left) || 0;
    const startY = Number.parseFloat(element.style.top) || 0;
    let moved = false;
    if (hooks.start) hooks.start();
    element.classList.add("dragging");
    const move = (moveEvent) => {
      const nx = startX + (moveEvent.clientX - sx) / (view.k || 1);
      const ny = startY + (moveEvent.clientY - sy) / (view.k || 1);
      if (Math.abs(moveEvent.clientX - sx) + Math.abs(moveEvent.clientY - sy) > 2) moved = true;
      onMove(nx, ny);
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      try {
        handle.releasePointerCapture(event.pointerId);
      } catch (_error) {
        // уже отпущен
      }
      element.classList.remove("dragging");
      if (hooks.end) hooks.end(moved);
      if (onEnd) onEnd(moved);
      if (hooks.persist) hooks.persist();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
}

export function enableZoneDrag(zone, view, onMove, onEnd, hooks) {
  bindDrag(zone, zone.querySelector(".zlab"), view, onMove, () => onEnd && onEnd(), hooks);
}

export function enableNodeDrag(card, view, onMove, onEnd, hooks) {
  bindDrag(card, card.querySelector(".hd"), view, onMove, () => onEnd && onEnd(), hooks);
}

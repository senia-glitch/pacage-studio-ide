function bindDrag(element, handle, view, onMove, onEnd, hooks = {}) {
  if (!handle) return;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (element.classList.contains("dragging")) return;
    if (event.target.closest("button,input,select,textarea,.no-drag")) return;
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
    const scale = view.k || 1;
    const pointerId = event.pointerId;
    const eventTarget = typeof window === "undefined" ? handle : window;
    let moved = false;
    let frame = 0;
    let latest = null;
    const beginDrag = () => {
      if (moved) return;
      moved = true;
      if (hooks.start) hooks.start();
      element.classList.add("dragging");
    };
    const applyMove = () => {
      frame = 0;
      if (!latest || !moved) return;
      onMove(startX + (latest.x - sx) / scale, startY + (latest.y - sy) / scale);
    };
    const move = (moveEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      latest = {x: moveEvent.clientX, y: moveEvent.clientY};
      if (!moved && Math.hypot(latest.x - sx, latest.y - sy) < 3) return;
      beginDrag();
      if (!frame) frame = requestAnimationFrame(applyMove);
    };
    const up = (upEvent) => {
      if (upEvent.pointerId !== pointerId) return;
      if (Number.isFinite(upEvent?.clientX) && Number.isFinite(upEvent?.clientY)) {
        latest = {x: upEvent.clientX, y: upEvent.clientY};
        if (!moved && Math.hypot(latest.x - sx, latest.y - sy) >= 3) beginDrag();
      }
      if (frame) {
        cancelAnimationFrame(frame);
      }
      if (moved) applyMove();
      eventTarget.removeEventListener("pointermove", move);
      eventTarget.removeEventListener("pointerup", up);
      eventTarget.removeEventListener("pointercancel", up);
      try {
        handle.releasePointerCapture(event.pointerId);
      } catch (_error) {
        // уже отпущен
      }
      element.classList.remove("dragging");
      if (moved) {
        if (hooks.end) hooks.end(true);
        if (onEnd) onEnd(true);
        if (hooks.persist) hooks.persist();
      }
    };
    eventTarget.addEventListener("pointermove", move);
    eventTarget.addEventListener("pointerup", up);
    eventTarget.addEventListener("pointercancel", up);
  });
}

export function enableZoneDrag(zone, view, onMove, onEnd, hooks) {
  bindDrag(zone, zone.querySelector(".zlab"), view, onMove, () => onEnd && onEnd(), hooks);
}

export function enableNodeDrag(card, view, onMove, onEnd, hooks) {
  bindDrag(card, card, view, onMove, () => onEnd && onEnd(), hooks);
}

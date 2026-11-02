export function rectsOverlap(a, b, pad = 6) {
  return a.x < b.x + b.w + pad && a.x + a.w + pad > b.x && a.y < b.y + b.h + pad && a.y + a.h + pad > b.y;
}

export function areaSize(rects) {
  let w = 240;
  let h = 70;
  for (const rect of rects || []) {
    w = Math.max(w, rect.x + rect.w + 14);
    h = Math.max(h, rect.y + rect.h + 14);
  }
  return {w, h};
}

export function proposedOverlap(proposed, others, pad = 6) {
  return proposed.some((rect) => others.some((other) => rectsOverlap(rect, other, pad)));
}

export function clampGroupDelta(moving, stationary, current, target, gap = 12) {
  const stepX = target.x - current.x;
  const stepY = target.y - current.y;
  let safeX = stepX;
  for (const rect of moving) {
    const x = rect.x + current.x;
    const y = rect.y + current.y;
    for (const obstacle of stationary) {
      if (y >= obstacle.y + obstacle.h + gap || y + rect.h + gap <= obstacle.y) continue;
      if (stepX > 0 && x + rect.w + gap <= obstacle.x) {
        safeX = Math.min(safeX, obstacle.x - gap - x - rect.w);
      } else if (stepX < 0 && x >= obstacle.x + obstacle.w + gap) {
        safeX = Math.max(safeX, obstacle.x + obstacle.w + gap - x);
      }
    }
  }

  let safeY = stepY;
  for (const rect of moving) {
    const x = rect.x + current.x + safeX;
    const y = rect.y + current.y;
    for (const obstacle of stationary) {
      if (x + rect.w + gap <= obstacle.x || x >= obstacle.x + obstacle.w + gap) continue;
      if (stepY > 0 && y + rect.h + gap <= obstacle.y) {
        safeY = Math.min(safeY, obstacle.y - gap - y - rect.h);
      } else if (stepY < 0 && y >= obstacle.y + obstacle.h + gap) {
        safeY = Math.max(safeY, obstacle.y + obstacle.h + gap - y);
      }
    }
  }
  return {x: current.x + safeX, y: current.y + safeY};
}

export function zoneOverlaps(rect, zones, pad = 10) {
  return (zones || []).some((other) => rectsOverlap(rect, other, pad));
}

export function proposedZoneRect(zone, size) {
  return {
    x: Number.parseFloat(zone.style.left) || 0,
    y: Number.parseFloat(zone.style.top) || 0,
    w: size.w,
    h: size.h,
  };
}

export function methodOrder(method, order = ["GET", "POST", "PUT", "PATCH", "DELETE"]) {
  const index = order.indexOf(method);
  return index < 0 ? 99 : index;
}



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

export function inferEdgeKind(fromKind, toKind) {
  if (fromKind === "endpoint" && toKind === "scenario") return "dispatch";
  if (fromKind === "scenario" && toKind === "scenario") return "scenario_call";
  if (fromKind === "scenario" && toKind === "io") return "port_call";
  if (fromKind === "io" && toKind === "model") return "table";
  if (fromKind === "test") return "tests";
  return "uses";
}

export function groupByMethodDomain(endpoints) {
  const groups = new Map();
  for (const endpoint of endpoints || []) {
    const details = endpoint.details || {};
    const method = String(details.http_method || "").toUpperCase() || "Метод не указан";
    const domain = String(endpoint.domain || "").trim() || "Без домена";
    if (!groups.has(method)) groups.set(method, new Map());
    const byDomain = groups.get(method);
    if (!byDomain.has(domain)) byDomain.set(domain, []);
    byDomain.get(domain).push(endpoint);
  }
  return groups;
}

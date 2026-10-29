export function offsetInStage(el, stage) {
  let x = 0;
  let y = 0;
  let current = el;
  while (current && current !== stage) {
    x += current.offsetLeft || 0;
    y += current.offsetTop || 0;
    current = current.offsetParent;
  }
  return {x, y};
}

export function measure(stage) {
  const out = {};
  stage.querySelectorAll(".card[data-id]").forEach((element) => {
    const position = offsetInStage(element, stage);
    out[element.dataset.id] = {
      x: position.x,
      y: position.y,
      w: element.offsetWidth,
      h: element.offsetHeight,
    };
  });
  return out;
}

export function sizeStage(stage, svg) {
  let width = 900;
  let height = 600;
  stage.querySelectorAll(".card,.zone,.colhead").forEach((element) => {
    width = Math.max(width, (Number.parseFloat(element.style.left) || 0) + element.offsetWidth + 80);
    height = Math.max(height, (Number.parseFloat(element.style.top) || 0) + element.offsetHeight + 80);
  });
  stage.style.width = `${width}px`;
  stage.style.height = `${height}px`;
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
}

export function clearStage(stage, svg) {
  for (const child of [...stage.children]) {
    if (child !== svg) child.remove();
  }
  svg.innerHTML = "";
}

function borderPoint(rect, tx, ty) {
  const cx = rect.x + rect.w / 2;
  const cy = rect.y + rect.h / 2;
  const dx = tx - cx;
  const dy = ty - cy;
  if (dx === 0 && dy === 0) return {x: cx, y: cy};
  const scale = Math.min((rect.w / 2) / Math.abs(dx), (rect.h / 2) / Math.abs(dy));
  return {x: cx + dx * scale, y: cy + dy * scale};
}

export function drawEdges(svg, boxes, edges, colors, names, escapeHtml) {
  let defs = "<defs>";
  for (const [kind, color] of Object.entries(colors)) {
    defs +=
      `<marker id="ar-${kind}" viewBox="0 0 10 10" refX="9" refY="5" ` +
      'markerWidth="6" markerHeight="6" orient="auto">' +
      `<path d="M0,0 L10,5 L0,10 z" fill="${color}"/></marker>`;
  }
  defs += "</defs>";
  const paths = edges
    .map((edge) => {
      const from = boxes[edge.from_node];
      const to = boxes[edge.to_node];
      if (!from || !to) return "";
      const fcx = from.x + from.w / 2;
      const fcy = from.y + from.h / 2;
      const tcx = to.x + to.w / 2;
      const tcy = to.y + to.h / 2;
      const dx = tcx - fcx;
      const dy = tcy - fcy;
      const dist = Math.hypot(dx, dy);
      let x1, y1, x2, y2;
      if (dist === 0) {
        x1 = from.x + from.w;
        y1 = fcy;
        x2 = to.x;
        y2 = tcy;
      } else {
        const p1 = borderPoint(from, tcx, tcy);
        const p2 = borderPoint(to, fcx, fcy);
        x1 = p1.x;
        y1 = p1.y;
        x2 = p2.x;
        y2 = p2.y;
      }
      let path;
      let mx;
      let my;
      if (dist === 0) {
        path = `M${x1},${y1} L${x2},${y2}`;
        mx = (x1 + x2) / 2;
        my = (y1 + y2) / 2 - 5;
      } else {
        const ux = dx / dist;
        const uy = dy / dist;
        const nx = -uy;
        const ny = ux;
        const k = Math.min(dist * 0.35, 70);
        const bow = Math.min(dist * 0.09, 14);
        const c1x = x1 + ux * k + nx * bow;
        const c1y = y1 + uy * k + ny * bow;
        const c2x = x2 - ux * k + nx * bow;
        const c2y = y2 - uy * k + ny * bow;
        path = `M${x1},${y1} C${c1x},${c1y} ${c2x},${c2y} ${x2},${y2}`;
        mx = (x1 + x2) / 2 + nx * bow * 0.75;
        my = (y1 + y2) / 2 + ny * bow * 0.75 - 5;
      }
      const dash = edge.yellow || edge.todo || edge.kind === "uses" || edge.kind === "depends" ? ' stroke-dasharray="6,4"' : "";
      const color = edge.yellow ? "#e8c33a" : (colors[edge.kind] || "#9a9aa2");
      return (
        `<path d="${path}" fill="none" stroke="${color}" stroke-width="1.7"${dash} ` +
        `marker-end="url(#ar-${edge.kind})"/>` +
        `<text class="elab" x="${mx}" y="${my}" text-anchor="middle">` +
        `${escapeHtml(names[edge.kind] || edge.kind)}</text>`
      );
    })
    .join("");
  svg.innerHTML = defs + paths;
}

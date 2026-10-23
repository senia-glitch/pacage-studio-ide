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
      const x1 = from.x + from.w;
      const y1 = from.y + from.h / 2;
      const x2 = to.x - 6;
      const y2 = to.y + to.h / 2;
      const path = `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2 - 5;
      const dash = edge.kind === "uses" || edge.kind === "depends" ? ' stroke-dasharray="6,4"' : "";
      const color = colors[edge.kind] || "#9a9aa2";
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

export function fuzzyScore(query, text) {
  const needle = String(query || "").trim().toLowerCase();
  const haystack = String(text || "").toLowerCase();
  if (!needle) return 0;
  let pos = 0;
  let score = 0;
  let streak = 0;
  for (const char of needle) {
    const found = haystack.indexOf(char, pos);
    if (found < 0) return -1;
    streak = found === pos ? streak + 1 : 0;
    score += 1 + streak + (found === 0 ? 2 : 0);
    pos = found + 1;
  }
  return Math.max(0, score - haystack.length * 0.001);
}

export function searchNodes(nodes, query, limit = 20) {
  const needle = String(query || "").trim();
  if (!needle) return [];
  const scored = [];
  for (const node of nodes || []) {
    const details = node.details || {};
    const text = [
      node.label,
      node.id,
      node.file_path,
      node.domain,
      node.description,
      details.function_name,
      details.class_name,
      details.test_name,
      details.url_path,
    ]
      .filter(Boolean)
      .join(" ");
    const score = fuzzyScore(needle, text);
    if (score >= 0) scored.push({node, score});
  }
  scored.sort((a, b) => b.score - a.score || String(a.node.label).localeCompare(String(b.node.label)));
  return scored.slice(0, limit).map((item) => item.node);
}

export function matchesAll(query, values) {
  const needle = String(query || "").trim().toLowerCase();
  if (!needle) return true;
  return values.some((value) => String(value || "").toLowerCase().includes(needle));
}

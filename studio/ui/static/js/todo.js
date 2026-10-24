const TODO_BADGE_NO_CODE = "TODO · нет кода";
const TODO_BADGE_NO_DESCRIPTION = "TODO · нет описания";

export const OFF_PATH_KINDS = ["scenario", "io", "model", "test"];
export const OFF_PATH_TITLES = {
  scenario: "Сценарии",
  io: "io",
  model: "Модели",
  test: "Тесты",
};

export function isTodo(node) {
  return !!node && node.planned === true;
}

function hasDescription(node) {
  return !!String((node && node.description) || "").trim();
}

export function todoBadge(node) {
  if (!isTodo(node)) return null;
  const hasText = hasDescription(node);
  return {
    text: hasText ? TODO_BADGE_NO_CODE : TODO_BADGE_NO_DESCRIPTION,
    variant: hasText ? "нет кода" : "нет описания",
  };
}

export function planPath(node) {
  return isTodo(node) ? String(node.file_path || "") : "";
}

export function anchorOf(node) {
  const details = (node && node.details) || {};
  if (!node) return "";
  if (node.kind === "endpoint") return String(details.function_name || "");
  if (node.kind === "scenario" || node.kind === "model") {
    return String(details.class_name || "");
  }
  if (node.kind === "test") return String(details.test_name || "");
  if (node.kind === "io") {
    const base = `${details.scenario_class || ""}._db.${details.method || ""}`;
    const ordinal = Number(details.ordinal) || 0;
    return ordinal > 0 ? `${base}#${ordinal}` : base;
  }
  if (node.kind === "nested") return String(details.name || node.label || "");
  return "";
}

export function placeNode(node) {
  if (!node) return {tab: "map", area: "offpath"};
  if (node.kind === "endpoint") return {tab: "map", area: "endpoints"};
  if (node.complete === true) return {tab: "scenario", area: "path"};
  return {tab: "map", area: "offpath"};
}

export function groupOffPath(nodes) {
  const groups = {scenario: [], io: [], model: [], test: []};
  for (const node of nodes || []) {
    if (!node || node.kind === "endpoint" || node.complete === true) continue;
    if (groups[node.kind]) groups[node.kind].push(node);
  }
  for (const kind of OFF_PATH_KINDS) {
    groups[kind].sort((a, b) =>
      String(a.label || a.id).localeCompare(String(b.label || b.id)),
    );
  }
  return groups;
}

export function offPathCount(groups) {
  return OFF_PATH_KINDS.reduce((total, kind) => total + (groups[kind] || []).length, 0);
}

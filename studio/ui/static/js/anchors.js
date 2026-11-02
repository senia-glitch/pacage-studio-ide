export function anchorOf(node) {
  const details = (node && node.details) || {};
  if (!node) return "";
  if (node.kind === "endpoint") return String(details.function_name || "");
  if (node.kind === "scenario" || node.kind === "model") return String(details.class_name || "");
  if (node.kind === "test") return String(details.test_name || "");
  if (node.kind === "io") {
    const base = `${details.scenario_class || ""}._db.${details.method || ""}`;
    const ordinal = Number(details.ordinal) || 0;
    return ordinal > 0 ? `${base}#${ordinal}` : base;
  }
  if (node.kind === "nested") return String(details.name || node.label || "");
  return "";
}

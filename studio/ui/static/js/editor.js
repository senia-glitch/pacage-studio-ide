export const EDITORS = [
  {id: "vscode", title: "VS Code", template: "vscode://file/{path}:{line}"},
];

function encodeEditorPath(value) {
  const text = String(value || "").replace(/\\/g, "/");
  const drive = /^([A-Za-z]):\//.exec(text);
  const prefix = drive ? drive[1] + ":/" : "";
  const rest = drive ? text.slice(prefix.length) : text;
  return prefix + rest.split("/").map((part) => encodeURIComponent(part)).join("/");
}

export function buildEditorUrl(editorId, absPath, relPath, line) {
  const preset = EDITORS.find((item) => item.id === editorId) || EDITORS[0];
  const pattern = preset.template;
  const full = encodeEditorPath(absPath);
  const rel = encodeEditorPath(relPath);
  return pattern
    .replaceAll("{path}", full)
    .replaceAll("{rel}", rel)
    .replaceAll("{line}", String(line || 1));
}


export const EDITORS = [
  {id: "vscode", title: "VS Code", template: "vscode://file/{path}:{line}"},
];

function encodeEditorPath(value) {
  return encodeURI(String(value || "").replace(/\\/g, "/"));
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


export const EDITORS = [
  {id: "vscode", title: "VS Code", template: "vscode://file/{path}:{line}"},
  {id: "vscode-insiders", title: "VS Code Insiders", template: "vscode-insiders://file/{path}:{line}"},
  {id: "cursor", title: "Cursor", template: "cursor://file/{path}:{line}"},
  {id: "windsurf", title: "Windsurf", template: "windsurf://file/{path}:{line}"},
  {id: "zed", title: "Zed", template: "zed://file/{path}:{line}"},
  {id: "pycharm", title: "PyCharm", template: "pycharm://open?file={path}&line={line}"},
  {id: "custom", title: "Своё", template: "{path}:{line}"},
];

export function encodeEditorPath(value) {
  return encodeURI(String(value || "").replace(/\\/g, "/"));
}

export function buildEditorUrl(editorId, absPath, relPath, line, template) {
  const preset = EDITORS.find((item) => item.id === editorId) || EDITORS[0];
  const pattern = editorId === "custom" && template ? String(template) : preset.template;
  const full = encodeEditorPath(absPath);
  const rel = encodeEditorPath(relPath);
  return pattern
    .replaceAll("{path}", full)
    .replaceAll("{rel}", rel)
    .replaceAll("{line}", String(line || 1));
}

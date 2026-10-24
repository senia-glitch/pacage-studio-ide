import assert from "node:assert/strict";
import test from "node:test";

import {buildEditorUrl} from "../../studio/ui/static/js/editor.js";

test("windows-путь с пробелом и кириллицей", () => {
  const url = buildEditorUrl(
    "vscode",
    "C:\\Users\\Arseniy\\Desktop\\PACAGE 4 IDE\\app\\x.py",
    "app/x.py",
    12,
  );
  assert.equal(
    url,
    "vscode://file/C:/Users/Arseniy/Desktop/PACAGE%204%20IDE/app/x.py:12",
  );
});

test("posix-путь и строка 1", () => {
  assert.equal(
    buildEditorUrl("vscode", "/home/me/app/x.py", "app/x.py", 1),
    "vscode://file//home/me/app/x.py:1",
  );
});

test("неизвестный редактор откатывается на VS Code", () => {
  assert.equal(
    buildEditorUrl("other", "/tmp/x.py", "x.py", 7),
    "vscode://file//tmp/x.py:7",
  );
});

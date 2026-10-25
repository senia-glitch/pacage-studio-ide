import assert from "node:assert/strict";
import test from "node:test";

import {S, applyGraph, cardHtml, esc, flushPending, hooks, setLive} from "../../studio/ui/static/js/core.js";

test("esc экранирует разметку и кавычки", () => {
  assert.equal(esc('<img src=x onerror=alert(1)>'), "&lt;img src=x onerror=alert(1)&gt;");
  assert.equal(esc('" onmouseover=alert(1)'), "&quot; onmouseover=alert(1)");
});

test("описание и критерии с разметкой выводятся буквально", () => {
  applyGraph({nodes: [], edges: [], scenario_paths: {}, scenarios: []});
  S.hideMeta = false;
  const node = {
    id: "endpoint:app/api/routes/a.py:a",
    kind: "endpoint",
    label: '<img src=x onerror=alert(1)>',
    file_path: "app/api/routes/a.py",
    line: 1,
    details: {function_name: "a"},
    description: '<img src=x onerror=alert(1)>',
    acceptance: ['" onmouseover=alert(1)'],
  };
  const {html} = cardHtml(node);
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&quot; onmouseover=alert(1)"));
  assert.ok(!html.includes('" onmouseover=alert(1)'));
});

test("имя файла и id в атрибутах экранируются", () => {
  const parent = {
    id: "endpoint:app/api/routes/a.py:a",
    kind: "endpoint",
    label: "a",
    file_path: "app/api/routes/a.py",
    line: 1,
    details: {function_name: "a"},
  };
  const nested = {
    id: 'nested:x:module:a" onmouseover=alert(1)',
    kind: "nested",
    label: '<b>вложенный</b>',
    file_path: 'a" onmouseover=alert(1).py',
    line: 2,
    details: {role: "module"},
  };
  applyGraph({
    nodes: [parent, nested],
    edges: [{from_node: parent.id, to_node: nested.id, kind: "uses"}],
    scenario_paths: {},
    scenarios: [],
  });
  const {html} = cardHtml(parent, {map: true});
  assert.ok(!html.includes('title="a" onmouseover='));
  assert.ok(!html.includes('data-nested="nested:x:module:a"'));
  assert.ok(html.includes("&quot; onmouseover=alert(1).py"));
  assert.ok(html.includes("&lt;b&gt;вложенный&lt;/b&gt;"));
});

test("баннер показывается при потере связи и скрывается при восстановлении", () => {
  const dot = {classList: {toggle() {}}, title: "", textContent: ""};
  const banner = {hidden: true};
  const previous = globalThis.document;
  globalThis.document = {
    getElementById: (id) =>
      id === "liveDot" ? dot : id === "offlineBanner" ? banner : null,
  };
  try {
    setLive(false);
    assert.equal(banner.hidden, false);
    setLive(true);
    assert.equal(banner.hidden, true);
  } finally {
    globalThis.document = previous;
  }
});

test("flushPending не пропускает смену вкладки при ошибке сохранения", async () => {
  S.formDirty = true;
  S.selectedNode = "endpoint:x";
  hooks.saveMetadata = async () => false;
  assert.equal(await flushPending(), false);
  hooks.saveMetadata = async () => true;
  assert.equal(await flushPending(), true);
  hooks.saveMetadata = async () => {};
  S.formDirty = false;
  S.selectedNode = null;
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  briefRequest,
  defaultMapMode,
  plannedNodes,
  plannedStats,
  shortPrompt,
  verifyLabel,
} from "../../studio/ui/static/js/briefmodel.js";

const graph = {
  nodes: [
    {id: "endpoint:a.py:x", kind: "endpoint", planned: true, description: "A"},
    {id: "scenario:b.py:C", kind: "scenario", planned: true, description: ""},
    {id: "model:c.py:M", kind: "model", planned: false},
  ],
};

test("планируемые узлы и счётчики", () => {
  assert.deepEqual(plannedNodes(graph).map((node) => node.id), [
    "scenario:b.py:C",
    "endpoint:a.py:x",
  ]);
  assert.deepEqual(plannedStats(graph), {tasks: 1, unready: 1, total: 2});
});

test("режим карты по умолчанию", () => {
  assert.equal(defaultMapMode(150), "full");
  assert.equal(defaultMapMode(151), "related");
});

test("запрос ТЗ не теряет пустой выбор", () => {
  assert.deepEqual(briefRequest({selected: [], plans: [], map: "none", includeEmpty: false}), {
    only: [],
    plans: [],
    map: "none",
    include_empty: false,
  });
  assert.deepEqual(briefRequest({selected: ["A"], plans: ["cancel_order"], map: "bad", includeEmpty: true}), {
    only: ["A"],
    plans: ["cancel_order"],
    map: "full",
    include_empty: true,
  });
});

test("чип N/M считает планы и старые узлы", () => {
  const mixed = {
    plans: [{slug: "refund", description: "План", layers: []}],
    nodes: [{id: "scenario:a.py:A", kind: "scenario", planned: true, description: "Старый"}],
  };
  assert.equal(plannedStats(mixed).total, 2);
  assert.equal(plannedStats(mixed).tasks, 2);
  assert.equal(verifyLabel({total: 2, done: 1, partial: 1, not_done: 0}, mixed).text, "Выполнено 1/2");
});

test("подписи чипа и промпт", () => {
  assert.equal(verifyLabel(null, graph).text, "К реализации: 1");
  assert.equal(verifyLabel(null, graph).urgent, true);
  const report = {total: 2, done: 1, partial: 1, not_done: 0};
  assert.equal(verifyLabel(report, graph).text, "Выполнено 1/2");
  assert.equal(verifyLabel(report, graph).urgent, true);
  assert.equal(
    shortPrompt(".studio/agent-brief.md"),
    "Прочитай и выполни ТЗ из файла .studio/agent-brief.md, следуй его правилам",
  );
});

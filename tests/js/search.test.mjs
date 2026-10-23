import assert from "node:assert/strict";
import test from "node:test";

import {fuzzyScore, matchesAll, searchNodes} from "../../studio/ui/static/js/search.js";

test("нечёткий поиск ранжирует точное вхождение выше", () => {
  assert.ok(fuzzyScore("ord", "orders") > fuzzyScore("ord", "xordes"));
  assert.equal(fuzzyScore("zzz", "orders"), -1);
});

test("поиск по узлам", () => {
  const nodes = [
    {label: "POST /orders", details: {url_path: "/api/v1/orders"}, file_path: "orders.py"},
    {label: "User", details: {class_name: "User"}, file_path: "models.py"},
  ];
  assert.equal(searchNodes(nodes, "orders")[0].label, "POST /orders");
  assert.equal(searchNodes(nodes, "zzz").length, 0);
  assert.equal(searchNodes(nodes, "").length, 0);
});

test("фильтр по подстроке", () => {
  assert.equal(matchesAll("ORD", ["POST /orders", "orders"]), true);
  assert.equal(matchesAll("billing", ["POST /orders"]), false);
});

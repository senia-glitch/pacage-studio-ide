import assert from "node:assert/strict";
import test from "node:test";

import {
  anchorOf,
  groupOffPath,
  placeNode,
  todoBadge,
} from "../../studio/ui/static/js/todo.js";

const nodes = [
  {
    id: "endpoint:app/routes.py:refund",
    kind: "endpoint",
    label: "refund",
    file_path: "app/routes.py",
    planned: true,
    description: "",
    complete: true,
    details: {function_name: "refund"},
  },
  {
    id: "scenario:app/scenarios/refund.py:Refund",
    kind: "scenario",
    label: "Refund",
    file_path: "app/scenarios/refund.py",
    planned: true,
    description: "Сценарий возврата.",
    complete: true,
    details: {class_name: "Refund", name: "Refund"},
  },
  {
    id: "scenario:app/scenarios/lost.py:Lost",
    kind: "scenario",
    label: "Lost",
    file_path: "app/scenarios/lost.py",
    planned: true,
    description: "",
    complete: false,
    details: {class_name: "Lost", name: "Lost"},
  },
  {
    id: "io:app/scenarios/lost.py:Lost._db.read",
    kind: "io",
    label: "read",
    file_path: "app/scenarios/lost.py",
    planned: true,
    description: "",
    complete: false,
    details: {scenario_class: "Lost", method: "read"},
  },
  {
    id: "model:models_lost.py:LostModel",
    kind: "model",
    label: "LostModel",
    file_path: "models_lost.py",
    planned: true,
    description: "",
    complete: false,
    details: {class_name: "LostModel", tablename: ""},
  },
  {
    id: "test:tests/test_lost.py:test_lost",
    kind: "test",
    label: "test_lost",
    file_path: "tests/test_lost.py",
    planned: true,
    description: "",
    complete: false,
    details: {test_name: "test_lost"},
  },
  {
    id: "endpoint:app/routes.py:ready",
    kind: "endpoint",
    label: "ready",
    file_path: "app/routes.py",
    planned: false,
    complete: true,
    details: {function_name: "ready"},
  },
  {
    id: "model:models.py:User",
    kind: "model",
    label: "User",
    file_path: "models.py",
    planned: false,
    complete: false,
    details: {class_name: "User", tablename: "users"},
  },
];

const graph = {nodes};

test("placeNode определяет место для каждого узла", () => {
  const places = Object.fromEntries(nodes.map((node) => [node.id, placeNode(node, graph)]));
  assert.deepEqual(places["endpoint:app/routes.py:refund"], {tab: "map", area: "endpoints"});
  assert.deepEqual(places["scenario:app/scenarios/refund.py:Refund"], {
    tab: "scenario",
    area: "path",
  });
  assert.deepEqual(places["scenario:app/scenarios/lost.py:Lost"], {
    tab: "map",
    area: "offpath",
  });
  assert.deepEqual(places["io:app/scenarios/lost.py:Lost._db.read"], {
    tab: "map",
    area: "offpath",
  });
  assert.deepEqual(places["model:models_lost.py:LostModel"], {tab: "map", area: "offpath"});
  assert.deepEqual(places["test:tests/test_lost.py:test_lost"], {
    tab: "map",
    area: "offpath",
  });
  for (const node of nodes) assert.ok(placeNode(node, graph), node.id);
});

test("todoBadge различает нет кода и нет описания", () => {
  assert.deepEqual(todoBadge(nodes[0]), {
    text: "TODO · нет описания",
    variant: "нет описания",
  });
  assert.deepEqual(todoBadge(nodes[1]), {
    text: "TODO · нет кода",
    variant: "нет кода",
  });
  assert.equal(todoBadge(nodes[6]), null);
});

test("groupOffPath группирует все внепутевые узлы по видам", () => {
  const groups = groupOffPath(nodes);
  assert.deepEqual(
    Object.fromEntries(Object.entries(groups).map(([kind, items]) => [kind, items.length])),
    {scenario: 1, io: 1, model: 2, test: 1},
  );
  assert.ok(groups.scenario.some((node) => node.id === "scenario:app/scenarios/lost.py:Lost"));
  assert.ok(!groups.model.some((node) => node.id === "endpoint:app/routes.py:refund"));
});

test("anchorOf повторяет якорь Python-сканера", () => {
  assert.equal(anchorOf(nodes[0]), "refund");
  assert.equal(anchorOf(nodes[1]), "Refund");
  assert.equal(anchorOf(nodes[3]), "Lost._db.read");
  assert.equal(anchorOf(nodes[5]), "test_lost");
});

import {OFF_PATH_KINDS, OFF_PATH_TITLES, isTodo, offPathCount, planPath} from "../../studio/ui/static/js/todo.js";

test("служебные подписи и граничные узлы", () => {
  assert.deepEqual(OFF_PATH_KINDS, ["scenario", "io", "model", "test"]);
  assert.equal(OFF_PATH_TITLES.model, "Модели");
  assert.equal(isTodo(null), false);
  assert.equal(planPath(nodes[0]), "app/routes.py");
  assert.equal(planPath(nodes[6]), "");
  assert.equal(anchorOf({kind: "nested", label: "N", details: {name: "N"}}), "N");
  assert.equal(anchorOf({kind: "nested", label: "N"}), "N");
  assert.equal(anchorOf({kind: "other", details: {}}), "");
  assert.deepEqual(placeNode(null), {tab: "map", area: "offpath"});
  assert.deepEqual(groupOffPath(null), {scenario: [], io: [], model: [], test: []});
  assert.equal(offPathCount(groupOffPath(nodes)), 5);
  assert.equal(offPathCount({}), 0);
});

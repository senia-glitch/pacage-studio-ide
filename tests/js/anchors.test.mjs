import assert from "node:assert/strict";
import test from "node:test";

import {anchorOf} from "../../studio/ui/static/js/anchors.js";

test("anchorOf builds stable anchors for scanned node kinds", () => {
  assert.equal(anchorOf({kind: "endpoint", details: {function_name: "get_order"}}), "get_order");
  assert.equal(anchorOf({kind: "scenario", details: {class_name: "OrderFlow"}}), "OrderFlow");
  assert.equal(anchorOf({kind: "io", details: {scenario_class: "OrderFlow", method: "read", ordinal: 2}}), "OrderFlow._db.read#2");
  assert.equal(anchorOf({kind: "test", details: {test_name: "test_order"}}), "test_order");
  assert.equal(anchorOf({kind: "nested", label: "module", details: {}}), "module");
  assert.equal(anchorOf(null), "");
});

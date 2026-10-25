import assert from "node:assert/strict";
import test from "node:test";

import {
  areaSize,
  methodOrder,
  proposedOverlap,
  rectsOverlap,
  zoneOverlaps,
} from "../../studio/ui/static/js/geometry.js";

test("пересечение прямоугольников", () => {
  const a = {x: 0, y: 0, w: 10, h: 10};
  assert.equal(rectsOverlap(a, {x: 5, y: 5, w: 10, h: 10}, 0), true);
  assert.equal(rectsOverlap(a, {x: 20, y: 0, w: 5, h: 5}, 0), false);
});

test("размер области по карточкам", () => {
  assert.deepEqual(areaSize([{x: 0, y: 0, w: 100, h: 50}]), {w: 240, h: 70});
  assert.deepEqual(areaSize([{x: 300, y: 100, w: 100, h: 50}]), {w: 414, h: 164});
});

test("предложенное пересечение и зоны", () => {
  assert.equal(proposedOverlap([{x: 0, y: 0, w: 10, h: 10}], [{x: 5, y: 5, w: 10, h: 10}], 0), true);
  assert.equal(zoneOverlaps({x: 0, y: 0, w: 10, h: 10}, [{x: 30, y: 30, w: 10, h: 10}], 0), false);
});

test("порядок методов", () => {
  assert.ok(methodOrder("GET") < methodOrder("POST"));
  assert.equal(methodOrder("BREW"), 99);
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  areaSize,
  clampGroupDelta,
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

test("перемещение ветки останавливается у первого препятствия", () => {
  const moving = [
    {x: 0, y: 0, w: 20, h: 20},
    {x: 30, y: 0, w: 20, h: 20},
  ];
  const stationary = [{x: 80, y: 0, w: 20, h: 20}];
  assert.deepEqual(
    clampGroupDelta(moving, stationary, {x: 0, y: 0}, {x: 100, y: 0}, 10),
    {x: 20, y: 0},
  );
});

test("ветка скользит вдоль препятствия и может обойти его", () => {
  const moving = [{x: 0, y: 0, w: 20, h: 20}];
  const stationary = [{x: 30, y: 0, w: 20, h: 20}];
  const first = clampGroupDelta(moving, stationary, {x: 0, y: 0}, {x: 50, y: 50}, 10);
  assert.deepEqual(first, {x: 0, y: 50});
  assert.deepEqual(
    clampGroupDelta(moving, stationary, first, {x: 50, y: 50}, 10),
    {x: 50, y: 50},
  );
});

test("ветка не пересекает препятствие при движении в обратную сторону", () => {
  const moving = [{x: 100, y: 40, w: 20, h: 20}];
  const stationary = [{x: 20, y: 40, w: 30, h: 20}];
  assert.deepEqual(
    clampGroupDelta(moving, stationary, {x: 0, y: 0}, {x: -100, y: 0}, 10),
    {x: -40, y: 0},
  );
});

test("порядок методов", () => {
  assert.ok(methodOrder("GET") < methodOrder("POST"));
  assert.equal(methodOrder("BREW"), 99);
});

import {proposedZoneRect} from "../../studio/ui/static/js/geometry.js";

test("прямоугольник зоны из стиля", () => {
  assert.deepEqual(proposedZoneRect({style: {left: "12.5px", top: "bad"}}, {w: 100, h: 50}), {x: 12.5, y: 0, w: 100, h: 50});
});

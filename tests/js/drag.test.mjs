import assert from "node:assert/strict";
import test from "node:test";

import {enableNodeDrag} from "../../studio/ui/static/js/drag.js";

class FakeCard {
  constructor() {
    this.listeners = new Map();
    this.classes = new Set();
    this.classList = {
      contains: (name) => this.classes.has(name),
      add: (name) => this.classes.add(name),
      remove: (name) => this.classes.delete(name),
    };
    this.style = {left: "100px", top: "200px"};
  }

  addEventListener(name, callback) {
    const listeners = this.listeners.get(name) || new Set();
    listeners.add(callback);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, callback) {
    this.listeners.get(name)?.delete(callback);
  }

  setPointerCapture() {}
  releasePointerCapture() {}

  emit(name, values = {}) {
    const event = {
      button: 0,
      pointerId: 1,
      target: {closest: () => null},
      stopPropagation() {},
      ...values,
    };
    for (const callback of [...(this.listeners.get(name) || [])]) callback(event);
  }
}

function withAnimationFrames(run) {
  const oldRequest = globalThis.requestAnimationFrame;
  const oldCancel = globalThis.cancelAnimationFrame;
  const callbacks = new Map();
  let id = 0;
  globalThis.requestAnimationFrame = (callback) => {
    const next = ++id;
    callbacks.set(next, callback);
    return next;
  };
  globalThis.cancelAnimationFrame = (key) => callbacks.delete(key);
  const flush = () => {
    const pending = [...callbacks.values()];
    callbacks.clear();
    pending.forEach(callback => callback());
  };
  try {
    run(flush);
  } finally {
    globalThis.requestAnimationFrame = oldRequest;
    globalThis.cancelAnimationFrame = oldCancel;
  }
}

test("drag ignores click jitter, batches moves, and converts by fixed zoom", () => {
  withAnimationFrames((flush) => {
    const card = new FakeCard();
    const positions = [];
    const lifecycle = [];
    enableNodeDrag(card, {k: 2}, (x, y) => positions.push({x, y}), null, {
      start: () => lifecycle.push("start"),
      end: () => lifecycle.push("end"),
      persist: () => lifecycle.push("persist"),
    });
    card.emit("pointerdown", {clientX: 10, clientY: 10});
    card.emit("pointermove", {clientX: 11, clientY: 11});
    assert.deepEqual(positions, []);
    assert.equal(card.classList.contains("dragging"), false);

    card.emit("pointermove", {clientX: 20, clientY: 30});
    card.emit("pointermove", {clientX: 30, clientY: 50});
    assert.deepEqual(positions, []);
    flush();
    assert.deepEqual(positions, [{x: 110, y: 220}]);
    card.emit("pointerup");
    assert.deepEqual(lifecycle, ["start", "end", "persist"]);
  });
});

test("pointer up applies a queued final move before finishing", () => {
  withAnimationFrames(() => {
    const card = new FakeCard();
    const positions = [];
    enableNodeDrag(card, {k: 1}, (x, y) => positions.push({x, y}));
    card.emit("pointerdown", {clientX: 0, clientY: 0});
    card.emit("pointermove", {clientX: 10, clientY: 12});
    card.emit("pointerup", {clientX: 12, clientY: 14});
    assert.deepEqual(positions, [{x: 112, y: 214}]);
  });
});

test("pointer up starts a drag when the final position crosses the threshold", () => {
  withAnimationFrames(() => {
    const card = new FakeCard();
    const positions = [];
    enableNodeDrag(card, {k: 1}, (x, y) => positions.push({x, y}));
    card.emit("pointerdown", {clientX: 0, clientY: 0});
    card.emit("pointerup", {clientX: 4, clientY: 0});
    assert.deepEqual(positions, [{x: 104, y: 200}]);
  });
});

test("click without drag does not start persistence lifecycle", () => {
  withAnimationFrames(() => {
    const card = new FakeCard();
    const lifecycle = [];
    enableNodeDrag(card, {k: 1}, () => assert.fail("click must not move"), null, {
      start: () => lifecycle.push("start"),
      end: () => lifecycle.push("end"),
      persist: () => lifecycle.push("persist"),
    });
    card.emit("pointerdown", {clientX: 5, clientY: 5});
    card.emit("pointerup", {clientX: 5, clientY: 5});
    assert.deepEqual(lifecycle, []);
  });
});

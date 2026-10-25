import assert from "node:assert/strict";
import test from "node:test";

import {
  cleanPositions,
  createStorage,
  hashText,
  storageKey,
} from "../../studio/ui/static/js/storage.js";

function memoryStore() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => {
      if (value === "BROKEN") throw new Error("quota");
      data.set(key, value);
    },
    removeItem: (key) => data.delete(key),
  };
}

test("запись, чтение и сброс", () => {
  const store = createStorage(memoryStore(), storageKey("/tmp/app"));
  assert.equal(store.load(), null);
  assert.equal(store.save({tab: "map"}), true);
  assert.deepEqual(store.load(), {tab: "map"});
  assert.equal(store.reset(), true);
  assert.equal(store.load(), null);
});

test("битый JSON игнорируется", () => {
  const memory = memoryStore();
  memory.setItem("k", "{not json");
  assert.equal(createStorage(memory, "k").load(), null);
});

test("ключ зависит от пути", () => {
  assert.notEqual(hashText("/a"), hashText("/b"));
  assert.match(storageKey("/a"), /^studio:v2:/);
});

test("позиции несуществующих узлов отбрасываются", () => {
  const result = cleanPositions(
    {"scope|keep": {x: 1, y: 2}, "scope|gone": {x: 3, y: 4}, "incomplete|gone": {x: 5, y: 6}},
    new Set(["keep"]),
    new Set(["incomplete"]),
  );
  assert.equal(result["scope|keep"].x, 1);
  assert.equal(result["scope|gone"], undefined);
  assert.equal(result["incomplete|gone"].x, 5);
});


test("ошибки записи и сброса не бросают", () => {
  const bad = {
    getItem: () => null,
    setItem: () => { throw new Error("quota"); },
    removeItem: () => { throw new Error("denied"); },
  };
  const store = createStorage(bad, "k");
  assert.equal(store.save({x: 1}), false);
  assert.equal(store.reset(), false);
});

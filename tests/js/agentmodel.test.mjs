import assert from "node:assert/strict";
import test from "node:test";

import {briefState, connectionText, tasksWord} from "../../studio/ui/static/js/agentmodel.js";

test("connectionText: адрес, токен, режим и подсказка", () => {
  const lines = connectionText("http://127.0.0.1:8777", "TOKEN", "plan").split("\n");
  assert.equal(lines[0], "Studio: http://127.0.0.1:8777");
  assert.equal(lines[1], "Token: TOKEN");
  assert.equal(lines[2], "Mode: plan");
  assert.ok(lines[3].includes("studio_connect"));
  assert.ok(!lines.join("\n").includes("/api/"), "строка не должна отсылать к HTTP");
});

test("tasksWord: склонение «задача»", () => {
  assert.deepEqual([0, 1, 2, 5, 11, 21, 24].map(tasksWord),
    ["задач", "задача", "задачи", "задач", "задач", "задача", "задачи"]);
});

test("briefState: сохранено, не сохранено, не собрано", () => {
  assert.equal(
    briefState(3, 1),
    "ТЗ бэкенда: 3 задачи · ТЗ фронтенда: 1 задача",
  );
  const text = briefState(null, null);
  assert.ok(text.includes("ТЗ бэкенда: не сохранено"));
  assert.ok(text.includes("ТЗ фронтенда: не собрано"));
});

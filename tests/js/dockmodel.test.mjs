import assert from "node:assert/strict";
import test from "node:test";

import {
  DOCK_STEPS,
  activeDockStep,
  agentStatus,
  canRollback,
  dockActions,
  dockNote,
  dockSteps,
  gitNote,
} from "../../studio/ui/static/js/dockmodel.js";

const ready = {claude_found: true, enabled: true, state: "idle"};

test("семь шагов, у каждого виден чей ход", () => {
  assert.deepEqual(DOCK_STEPS.map((s) => s.id),
    ["goal", "ideas", "review", "select", "implement", "verify", "accept"]);
  const turns = Object.fromEntries(dockSteps({stage: "mode"}).map((s) => [s.id, s.turnLabel]));
  assert.equal(turns.goal, "ваш ход");
  assert.equal(turns.ideas, "ход агента");
  assert.equal(turns.verify, "проверяет студия");
});

test("внутренние этапы сходятся к шагам человека", () => {
  const step = (stage, extra = {}) => activeDockStep({stage, ...extra});
  assert.equal(step("mode"), "goal");
  assert.equal(step("review"), "review");
  assert.equal(step("confirm"), "review"); // перенос делает свайп вверх
  assert.equal(step("select"), "select");
  assert.equal(step("report"), "accept");
  assert.equal(step("report", {tests: {state: "running"}}), "verify");
  assert.equal(activeDockStep(null), "goal");
});

test("состояния шагов: сделано, активно, впереди; путь реализации пропускает идеи и разбор", () => {
  const states = (flow) => Object.fromEntries(dockSteps(flow).map((s) => [s.id, s.state]));
  assert.deepEqual(states({stage: "select", mode: "plan"}), {
    goal: "done", ideas: "done", review: "done", select: "active",
    implement: "todo", verify: "todo", accept: "todo",
  });
  const skipped = states({stage: "implement", mode: "implement"});
  assert.equal(skipped.ideas, "skipped");
  assert.equal(skipped.review, "skipped");
  assert.equal(skipped.implement, "active");
  assert.equal(states({stage: "mode", mode: ""}).goal, "active");
});

test("индикатор связи: не найден, не подключён, работает, ждёт, сбой, готов", () => {
  assert.equal(agentStatus(null).tone, "off");
  assert.equal(agentStatus({claude_found: false}).tone, "bad");
  assert.equal(agentStatus({claude_found: true, enabled: false}).text, "не подключён");
  assert.equal(agentStatus({...ready, state: "running"}).tone, "run");
  assert.equal(agentStatus({...ready, state: "queued"}).tone, "run");
  assert.equal(agentStatus({...ready, state: "waiting_user"}).tone, "wait");
  const failed = agentStatus({...ready, state: "failed", message: "строка1\nпоследняя"});
  assert.equal(failed.tone, "bad");
  assert.match(failed.text, /последняя/);
  assert.equal(agentStatus({...ready, state: "stalled"}).tone, "bad");
  const ok = agentStatus(ready);
  assert.equal(ok.tone, "ok");
  assert.equal(ok.connected, true);
});

test("кнопки окна зависят от шага и состояния агента", () => {
  const ids = (flow, view = ready) => dockActions(flow, view).map((a) => a.id);
  assert.deepEqual(ids({stage: "mode"}, {claude_found: true, enabled: false}), ["settings"]);
  assert.deepEqual(ids({stage: "mode"}), ["start"]);
  assert.deepEqual(ids({stage: "ideas", cards: {total: 0}}), []);
  assert.deepEqual(ids({stage: "ideas", cards: {total: 3}}), ["to-review"]);
  assert.deepEqual(ids({stage: "ideas", cards: {total: 0}}, {...ready, state: "failed"}), ["retry"]);
  assert.deepEqual(ids({stage: "review", cards: {pending: 2}}), ["goto-ideas"]);
  assert.deepEqual(ids({stage: "review", cards: {pending: 0, total: 2}}), ["to-select", "goto-ideas"]);
  assert.deepEqual(ids({stage: "select"}), ["select"]);
  assert.deepEqual(ids({stage: "implement"}), []);
  assert.deepEqual(ids({stage: "report"}), ["report", "rework", "finish"]);
  assert.deepEqual(ids({stage: "report", tests: {state: "running"}}), []);
  assert.equal(dockActions({stage: "report"}, ready).find((a) => a.id === "finish").primary, true);
  assert.deepEqual(dockActions(null, ready), []);
});

test("подсказки по шагам и вопрос агента", () => {
  assert.match(dockNote({stage: "mode"}, ready), /Начать/);
  assert.match(dockNote({stage: "mode"}, {claude_found: true, enabled: false}), /Настройки агента/);
  assert.match(dockNote({stage: "review", cards: {pending: 2}}, ready), /вверх/);
  assert.match(dockNote({stage: "review", cards: {pending: 0}}, ready), /разобраны/);
  assert.match(dockNote({stage: "select"}, {...ready, state: "waiting_user"}), /вопрос/);
});

test("откат и строка про git", () => {
  assert.equal(canRollback({git: {can_rollback: true}}), true);
  assert.equal(canRollback({git: {}}), false);
  assert.equal(canRollback(null), false);
  assert.match(gitNote({git: {repo: false}}), /вне репозитория/);
  assert.match(gitNote({git: {repo: true, branch: "studio/c1", checkpoint: "abc"}}), /чекпоинт/);
  assert.equal(gitNote({git: {repo: true, branch: "main"}, git_note: "есть правки"}), "есть правки");
});

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

const ready = {claude_found: true, enabled: true, state: "idle", mcp: {ok: true}};

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

test("«готов» только после успешного рукопожатия с MCP-сервером", () => {
  const unchecked = agentStatus({claude_found: true, enabled: true, state: "idle", mcp: {ok: null}});
  assert.equal(unchecked.tone, "wait");
  assert.notEqual(unchecked.tone, "ok");
  assert.equal(agentStatus({claude_found: true, enabled: true, state: "idle"}).tone, "wait");
  const broken = agentStatus({claude_found: true, enabled: true, state: "idle",
    mcp: {ok: false, code: "no_mcp", reason: "Не установлена библиотека mcp.\nПодробности"}});
  assert.equal(broken.tone, "bad");
  assert.equal(broken.connected, false);
  assert.match(broken.text, /MCP не запускается: Не установлена библиотека mcp/);
  // сбой при подключении: агент не включён, но причина видна
  const notEnabled = agentStatus({claude_found: true, enabled: false,
    mcp: {ok: false, code: "python_missing", reason: "Путь устарел"}});
  assert.equal(notEnabled.tone, "bad");
  // «нет конфига» — это просто «не подключён»
  assert.equal(agentStatus({claude_found: true, enabled: false,
    mcp: {ok: false, code: "no_config"}}).text, "не подключён");
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

import {agentInfo} from "../../studio/ui/static/js/dockmodel.js";

test("выбранный агент: название, найден ли, нужна ли модель", () => {
  assert.deepEqual(agentInfo(null), {name: "claude", label: "Claude Code", found: false, needsModel: false});
  assert.equal(agentInfo({claude_found: true}).found, true);
  const agents = [
    {name: "claude", label: "Claude Code", found: true, needs_model: false},
    {name: "opencode", label: "OpenCode", found: true, needs_model: true},
  ];
  const info = agentInfo({agent: "opencode", agents});
  assert.equal(info.label, "OpenCode");
  assert.equal(info.needsModel, true);
});

test("индикатор OpenCode: без модели — «выберите модель», с моделью — как у Claude", () => {
  const agents = [
    {name: "claude", label: "Claude Code", found: true, needs_model: false},
    {name: "opencode", label: "OpenCode", found: true, needs_model: true},
  ];
  const base = {agent: "opencode", agents, enabled: true, mcp: {ok: true}, state: "idle"};
  assert.equal(agentStatus({...base, model: ""}).text, "выберите модели");
  assert.equal(agentStatus({...base, model: ""}).connected, false);
  assert.equal(agentStatus({...base, model: "opencode-go/kimi-k3"}).tone, "ok");
  const missing = agents.map((a) => (a.name === "opencode" ? {...a, found: false} : a));
  assert.equal(agentStatus({...base, agents: missing}).text, "OpenCode не найден");
});

import {agentTitle, roleViews} from "../../studio/ui/static/js/dockmodel.js";

const roleView = (plan, implement, extra = {}) => ({
  enabled: true, mcp: {ok: true}, state: "idle", ...extra,
  roles: {
    plan: {title: "План", found: true, needs_model: false, model: "", reason: "", label: "Claude Code", ...plan},
    implement: {title: "Реализация", found: true, needs_model: true, model: "", reason: "", label: "OpenCode", ...implement},
  },
});

test("исполнители: заголовок окна и состояние по ролям", () => {
  const mixed = roleView({}, {model: "opencode-go/kimi-k3"});
  assert.equal(agentTitle(mixed), "План: Claude Code · Реализация: OpenCode");
  assert.equal(agentTitle(roleView({}, {label: "Claude Code", needs_model: false})), "Claude Code");
  assert.deepEqual(roleViews(mixed).map((item) => item.role), ["plan", "implement"]);
  assert.equal(agentStatus(mixed).tone, "ok");
  // модель нужна только роли, чей агент её требует
  assert.equal(agentStatus(roleView({}, {model: ""})).text, "выберите модель (Реализация)");
  assert.equal(agentStatus(roleView({needs_model: true, label: "OpenCode"}, {model: ""})).text, "выберите модели");
  // неготовый агент любой роли блокирует связь
  assert.equal(agentStatus(roleView({found: false}, {model: "a/b"})).text, "Claude Code не найден");
  assert.equal(agentStatus(roleView({}, {found: false, model: "a/b"})).connected, false);
});

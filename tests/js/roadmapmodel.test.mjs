import assert from "node:assert/strict";
import test from "node:test";

import {
  autoKey,
  autoOpen,
  cardsOf,
  defaultSelection,
  pendingQueue,
  primaryAction,
  progressOf,
  selectionCount,
  selectionPayload,
  stageNote,
  swipeDecision,
  swipeProgress,
  tally,
} from "../../studio/ui/static/js/roadmapmodel.js";

test("swipeDecision: вверх принимает, вниз отклоняет, короткий жест возвращает", () => {
  assert.equal(swipeDecision(-120), "accepted");
  assert.equal(swipeDecision(140), "rejected");
  assert.equal(swipeDecision(-60), null);
  assert.equal(swipeDecision(60), null);
  assert.equal(swipeDecision(0), null);
  // быстрый бросок срабатывает уже на половине пути
  assert.equal(swipeDecision(-60, -0.9), "accepted");
  assert.equal(swipeDecision(60, 0.9), "rejected");
  assert.equal(swipeDecision(-20, -0.9), null);
  assert.equal(swipeDecision(undefined), null);
});

test("swipeProgress: знак и сила жеста ограничены 0…1", () => {
  assert.deepEqual(swipeProgress(-55), {ratio: -0.5, side: "accepted", strength: 0.5});
  assert.equal(swipeProgress(500).side, "rejected");
  assert.equal(swipeProgress(500).strength, 1);
  assert.equal(swipeProgress(0).side, "");
});

const BOARD = {
  ideas: [
    {
      title: "Каталог",
      scenarios: [
        {id: "s1", title: "Список", description: "Читает", acceptance: ["a", " "], domain: "d", decision: "pending",
          source: {slug: "list_x", http_method: "get", path: "/x", uses_db: true}},
        {id: "s2", title: "Создать", decision: "accepted", source: {slug: "create_x", http_method: "post", path: "/x"}},
        {id: "old", title: "Без решения", source: {}},
      ],
      pages: [
        {id: "p1", title: "Страница", name: "x-list", route: "/x", decision: "rejected",
          calls: ["GET /x"], blocks: [{name: "Row", description: "строка"}]},
      ],
    },
    "мусор",
  ],
};

test("cardsOf: только карточки цепочки, с готовыми полями для показа", () => {
  const cards = cardsOf(BOARD);
  assert.deepEqual(cards.map((card) => card.id), ["s1", "s2", "p1"]);
  const first = cards[0];
  assert.equal(first.kindLabel, "Сценарий бэкенда");
  assert.equal(first.route, "GET /x");
  assert.equal(first.slug, "list_x");
  assert.deepEqual(first.acceptance, ["a"]);
  assert.deepEqual(first.extras, ["работает с БД"]);
  assert.equal(first.idea, "Каталог");
  const page = cards[2];
  assert.equal(page.kindLabel, "Страница фронтенда");
  assert.equal(page.route, "/x");
  assert.deepEqual(page.calls, ["GET /x"]);
  assert.deepEqual(page.blocks, [{name: "Row", description: "строка"}]);
  assert.deepEqual(cardsOf(null), []);
});

test("pendingQueue и tally считают решения", () => {
  assert.deepEqual(pendingQueue(BOARD).map((card) => card.id), ["s1"]);
  assert.deepEqual(tally(cardsOf(BOARD)), {pending: 1, accepted: 1, rejected: 1, total: 3});
});

test("primaryAction и stageNote по этапам", () => {
  const base = {cycle: 1, cards: {total: 0, pending: 0, accepted: 0}, agent: {connected: false}};
  assert.equal(primaryAction({...base, stage: "mode"}).id, "mode");
  assert.equal(primaryAction({...base, stage: "mode"}).label, "Как подключить агента");
  assert.equal(primaryAction({...base, stage: "mode", agent: {connected: true}}).label, "Выбрать режим");
  assert.equal(primaryAction({...base, stage: "ideas"}), null);
  assert.equal(primaryAction({...base, stage: "ideas", cards: {total: 2}}).id, "review");
  assert.equal(primaryAction({...base, stage: "review"}).id, "swipe");
  assert.equal(primaryAction({...base, stage: "confirm"}).id, "confirm");
  assert.equal(primaryAction({...base, stage: "select"}).id, "select");
  assert.equal(primaryAction({...base, stage: "implement"}).id, "manual");
  assert.equal(primaryAction({...base, stage: "report"}).id, "report");
  assert.equal(primaryAction(null), null);
  assert.match(stageNote({...base, stage: "review", cards: {pending: 4}}), /4/);
  assert.match(stageNote({...base, stage: "mode"}), /Запустите агента/);
  assert.equal(stageNote({stage: "неизвестно"}), "");
});

test("autoOpen: окно открывается само, но не раньше подключения агента", () => {
  assert.equal(autoOpen({stage: "mode", agent: {connected: false}}), "");
  assert.equal(autoOpen({stage: "mode", agent: {connected: true}}), "mode");
  assert.equal(autoOpen({stage: "ideas"}), "");
  assert.equal(autoOpen({stage: "review"}), "swipe");
  assert.equal(autoOpen({stage: "confirm"}), "confirm");
  assert.equal(autoOpen({stage: "select"}), "select");
  assert.equal(autoOpen({stage: "implement"}), "");
  assert.equal(autoOpen({stage: "report"}), "report");
  assert.equal(autoKey({stage: "review", cycle: 3, entries: 5}), "3:review:5");
  assert.notEqual(autoKey({stage: "review", cycle: 1, entries: 2}), autoKey({stage: "review", cycle: 1, entries: 4}));
  assert.equal(autoKey(null), "");
});

test("выбор задач: по умолчанию всё готовое, пустое отключено", () => {
  const candidates = {
    plans: [{slug: "a", ready: true}, {slug: "b", ready: false}],
    nodes: [{id: "n1", ready: true}],
    pages: [{id: "p1", ready: true}, {id: "p2", ready: true}],
  };
  const selection = defaultSelection(candidates);
  assert.deepEqual(selectionPayload(selection), {plans: ["a"], nodes: ["n1"], pages: ["p1", "p2"]});
  assert.equal(selectionCount(selection), 4);
  selection.pages.delete("p2");
  assert.equal(selectionCount(selection), 3);
  assert.equal(selectionCount(defaultSelection(null)), 0);
});

test("progressOf складывает части проверки", () => {
  assert.deepEqual(progressOf([{done: 1, total: 2}, null, {done: 2, total: 2}]), {done: 3, total: 4, complete: false});
  assert.equal(progressOf([{done: 2, total: 2}]).complete, true);
  assert.deepEqual(progressOf([]), {done: 0, total: 0, complete: false});
});

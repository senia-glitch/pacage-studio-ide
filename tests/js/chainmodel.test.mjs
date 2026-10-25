import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPayload,
  canCreate,
  emptyDraft,
  layerChips,
  normalizeDraft,
  statusLabel,
} from "../../studio/ui/static/js/chainmodel.js";

test("пустой черновик: флажки включены, создавать нельзя", () => {
  const draft = emptyDraft();
  assert.equal(draft.version, 2);
  assert.equal(draft.uses_db, true);
  assert.equal(draft.with_test, true);
  assert.equal(draft.title, "");
  assert.equal(canCreate(draft, null), false);
});

test("частично заполненный черновик нормализуется и обрезает пробелы", () => {
  const draft = normalizeDraft({
    title: "  Отмена заказа  ",
    description: "  Отменяет заказ.  ",
    acceptance: "  заказ отменён  ",
    domain: " orders ",
    http_method: "POST",
    path: " /orders/{id} ",
    slug: " cancel_order ",
  });
  assert.equal(draft.title, "Отмена заказа");
  assert.equal(draft.description, "Отменяет заказ.");
  assert.equal(draft.acceptance, "заказ отменён");
  assert.equal(draft.domain, "orders");
  assert.equal(draft.http_method, "post");
  assert.equal(draft.path, "/orders/{id}");
  assert.equal(draft.slug, "cancel_order");
  assert.equal(draft.uses_db, true);
  assert.equal(draft.with_test, true);
});

test("флажки читаются и по умолчанию включены", () => {
  const off = normalizeDraft({title: "A", uses_db: false, with_test: false});
  assert.equal(off.uses_db, false);
  assert.equal(off.with_test, false);
  const camel = normalizeDraft({title: "A", usesDb: false, withTest: false});
  assert.equal(camel.uses_db, false);
  assert.equal(camel.with_test, false);
});

test("старый черновик узлов и связей отбрасывается без ошибок", () => {
  const old = normalizeDraft({
    nodes: [{kind: "endpoint", name: "old"}],
    edges: [{from: "a", to: "b"}],
    seq: 3,
  });
  assert.deepEqual(old, emptyDraft());
  assert.deepEqual(normalizeDraft("broken"), emptyDraft());
  assert.deepEqual(normalizeDraft({nodes: []}), emptyDraft());
});

test("canCreate требует название, описание и отсутствие конфликтов", () => {
  const draft = {title: "Отмена", description: "Текст"};
  assert.equal(canCreate(draft, {ok: true, conflicts: []}), true);
  assert.equal(canCreate(draft, {ok: false, conflicts: [{code: "W_PLAN_CONFLICT"}]}), false);
  assert.equal(canCreate(draft, {ok: true, conflicts: [{code: "W_PLAN_CONFLICT"}]}), false);
  assert.equal(canCreate({title: "", description: "Текст"}, {ok: true, conflicts: []}), false);
  assert.equal(canCreate({title: "A", description: "  "}, {ok: true, conflicts: []}), false);
});

test("buildPayload собирает критерии по строкам", () => {
  const payload = buildPayload({
    title: " A ",
    description: " B ",
    acceptance: "- один\n\n два \n- три",
    slug: " a ",
    uses_db: false,
    with_test: false,
  });
  assert.deepEqual(payload, {
    title: "A",
    slug: "a",
    description: "B",
    acceptance: ["один", "два", "три"],
    domain: "",
    http_method: "",
    path: "",
    uses_db: false,
    with_test: false,
  });
});

test("layerChips показывает готовность всех статусов", () => {
  const chips = layerChips({
    layers: [
      {layer: "endpoint", status: "done", actual_names: ["cancel_order"], target_file: "a.py"},
      {layer: "scenario", status: "missing", suggested_name: "CancelOrderScenario", target_file: "b.py"},
    ],
  });
  assert.deepEqual(chips.map((chip) => chip.text), ["эндпоинт: готов", "сценарий: нет"]);
  assert.equal(chips[0].done, true);
  assert.equal(chips[1].done, false);
  assert.equal(chips[0].name, "cancel_order");
  assert.equal(chips[1].name, "CancelOrderScenario");
  assert.deepEqual(layerChips(null), []);
});

test("statusLabel знает все статусы плана", () => {
  assert.equal(statusLabel("done"), "выполнено");
  assert.equal(statusLabel("partial"), "частично");
  assert.equal(statusLabel("not_done"), "не сделано");
  assert.equal(statusLabel("unknown"), "не сделано");
});

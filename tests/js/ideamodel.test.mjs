import assert from "node:assert/strict";
import test from "node:test";

import {
  blockFrom,
  pageFrom,
  pageSource,
  pagesFrom,
  plural,
  railCounts,
  railLabel,
  strings,
  text,
} from "../../studio/ui/static/js/ideamodel.js";

test("plural: склонение по правилам русского языка", () => {
  const word = (n) => plural(n, "сценарий", "сценария", "сценариев");
  assert.deepEqual([0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 111].map(word), [
    "сценариев", "сценарий", "сценария", "сценария", "сценариев", "сценариев",
    "сценариев", "сценариев", "сценарий", "сценария", "сценариев", "сценариев",
  ]);
});

test("text и strings чистят вход", () => {
  assert.equal(text(5), "");
  assert.equal(text("а"), "а");
  assert.deepEqual(strings([" a ", "", "b", 3]), ["a", "b", "3"]);
  assert.deepEqual(strings("не массив"), []);
});

test("pageFrom: ответ LLM становится карточкой страницы", () => {
  const raw = {
    title: "Мои уроки", name: "my-lessons", description: "Список", acceptance: ["А", ""],
    domain: "клиент", route: "/my", calls: ["GET /lessons"],
    blocks: [{name: "Row", description: "Строка", calls: ["GET /x"]}, "мусор", null],
  };
  const page = pageFrom(raw, 0);
  assert.equal(page.title, "Мои уроки");
  assert.deepEqual(page.acceptance, ["А"]);
  assert.equal(page.blocks.length, 3);
  assert.equal(page.blocks[0].name, "Row");
  assert.equal(page.blocks[1].name, "Блок 2"); // не объект — получает имя по номеру
  assert.equal(page.mapPageId, "");
  assert.ok(page.id.startsWith("idea-"));
});

test("pagesFrom пропускает не-объекты, имя берётся из title/name", () => {
  assert.deepEqual(pagesFrom(null), []);
  const pages = pagesFrom([{name: "only-name"}, 5, null, {title: "T"}]);
  assert.deepEqual(pages.map((p) => p.title), ["only-name", "T"]);
  assert.equal(pageFrom({}, 2).title, "Страница 3");
});

test("карточка, сохранённая в доске, читается без потерь", () => {
  const card = pageFrom({title: "T", name: "t", calls: ["GET /a"], mapPageId: "page-plan:1",
    blocks: [{name: "B", mapBlockId: "block-plan:1", calls: ["GET /a"]}]}, 0);
  const again = pageFrom(JSON.parse(JSON.stringify(card)), 0);
  assert.equal(again.id, card.id);
  assert.equal(again.mapPageId, "page-plan:1");
  assert.equal(again.blocks[0].mapBlockId, "block-plan:1");
  assert.deepEqual(again.calls, ["GET /a"]);
});

test("pageSource: JSON для ответа собирается из полей карточки", () => {
  const page = pageFrom({title: "T", name: "t", description: "d", route: "/t", calls: ["GET /a"],
    extra: "сохраняется", blocks: [{name: "B", description: "bd", calls: []}]}, 0);
  page.description = "новое";
  const source = pageSource(page);
  assert.equal(source.description, "новое");
  assert.equal(source.extra, "сохраняется");
  assert.deepEqual(source.blocks, [{name: "B", description: "bd", acceptance: [], calls: []}]);
});

test("blockFrom: имя по номеру, если его нет", () => {
  assert.equal(blockFrom({}, 0).name, "Блок 1");
  assert.equal(blockFrom({title: "Заголовок"}, 4).name, "Заголовок");
});

test("railCounts и railLabel: счётчики и метка агента", () => {
  const idea = {scenarios: [1], pages: [1, 2], created_by: ""};
  assert.equal(railCounts(idea), "1 сценарий · 2 страницы");
  assert.equal(railLabel(idea), "1 сценарий · 2 страницы");
  assert.equal(railLabel({scenarios: [], pages: [], created_by: "agent"}), "0 сценариев · от агента");
});

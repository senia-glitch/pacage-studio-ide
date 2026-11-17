import assert from "node:assert/strict";
import test from "node:test";

import {keepScroll} from "../../studio/ui/static/js/util.js";

function panel(top) {
  const inner = {scrollTop: top[1] || 0};
  const root = {
    scrollTop: top[0] || 0,
    inner,
    querySelector: (sel) => (sel === ".inner" ? root.inner : null),
  };
  return root;
}

test("перерисовка возвращает прокрутку панели и вложенного блока", () => {
  const root = panel([120, 40]);
  const result = keepScroll(root, [":self", ".inner"], () => {
    root.scrollTop = 0; // innerHTML сбросил прокрутку
    root.inner = {scrollTop: 0}; // вложенный блок создан заново
    return "готово";
  });
  assert.equal(result, "готово");
  assert.equal(root.scrollTop, 120);
  assert.equal(root.inner.scrollTop, 40);
});

test("без панели просто выполняет перерисовку", () => {
  assert.equal(keepScroll(null, [":self"], () => 7), 7);
});

test("нулевую прокрутку и пропавший блок не трогает", () => {
  const root = panel([0, 0]);
  keepScroll(root, [":self", ".inner", ".нет"], () => { root.inner = null; });
  assert.equal(root.scrollTop, 0);
});

import assert from "node:assert/strict";
import test from "node:test";

import {esc, setLive} from "../../studio/ui/static/js/core.js";

test("esc экранирует разметку и кавычки", () => {
  assert.equal(esc('<img src=x onerror=alert(1)>'), "&lt;img src=x onerror=alert(1)&gt;");
  assert.equal(esc('" onmouseover=alert(1)'), "&quot; onmouseover=alert(1)");
});

test("баннер показывается при потере связи и скрывается при восстановлении", () => {
  const dot = {classList: {toggle() {}}, title: "", textContent: ""};
  const banner = {hidden: true};
  const previous = globalThis.document;
  globalThis.document = {
    getElementById: (id) =>
      id === "liveDot" ? dot : id === "offlineBanner" ? banner : null,
  };
  try {
    setLive(false);
    assert.equal(banner.hidden, false);
    setLive(true);
    assert.equal(banner.hidden, true);
  } finally {
    globalThis.document = previous;
  }
});

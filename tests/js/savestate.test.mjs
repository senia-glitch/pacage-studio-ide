import assert from "node:assert/strict";
import test from "node:test";

import {nextState, saveLabel} from "../../studio/ui/static/js/savestate.js";

test("все переходы формы", () => {
  let state = "idle";
  state = nextState(state, "edit");
  assert.equal(state, "dirty");
  state = nextState(state, "save");
  assert.equal(state, "saving");
  state = nextState(state, "saved");
  assert.equal(state, "saved");
  state = nextState(state, "edit");
  assert.equal(state, "dirty");
  state = nextState(state, "save");
  state = nextState(state, "fail");
  assert.equal(state, "error");
  assert.equal(saveLabel("conflict"), "Конфликт: файл изменён");
  assert.equal(nextState("unknown", "edit"), "idle");
});

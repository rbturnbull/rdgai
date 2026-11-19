import { test } from "node:test";
import assert from "node:assert/strict";
import { canUseReason, usableReason } from "../../rdgai/gui/static/logic.js";

test("a suggestion's reason can be used whichever direction it was written for", () => {
  assert.equal(canUseReason({ justification: "omits του", written_for: null }, false), true);
  assert.equal(canUseReason({ justification: "adds του", written_for: ["b", "a"] }, false), true);
});

test("no Use reason without a reason, or when read-only", () => {
  assert.equal(canUseReason({ justification: "", written_for: null }, false), false);
  assert.equal(canUseReason({ justification: null }, false), false);
  assert.equal(canUseReason({ justification: "omits του", written_for: null }, true), false);
});

test("the copied reason says which direction it was written for, when it is the other one", () => {
  assert.equal(usableReason({ justification: "omits του", written_for: null }), "omits του");
  assert.equal(usableReason({ justification: "adds του", written_for: ["b", "a"] }), "Written for b → a: adds του");
});

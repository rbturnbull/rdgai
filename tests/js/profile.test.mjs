// Tests for what the header shows for the annotator (logic.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { headerUser } from "../../rdgai/gui/static/logic.js";

test("the full name beside the short name", () => {
  assert.deepEqual(headerUser({ id: "RT", name: "Robert Turnbull", short_name: "RT" }), { name: "Robert Turnbull", short: "RT" });
});

test("a name that is the same as the short name is shown once", () => {
  assert.deepEqual(headerUser({ id: "RT", name: null, short_name: "RT" }), { name: null, short: "RT" });
  assert.deepEqual(headerUser({ id: "RT", name: "RT", short_name: "RT" }), { name: null, short: "RT" });
});

test("no annotator", () => {
  assert.equal(headerUser({ id: null, name: null, short_name: null }), null);
});

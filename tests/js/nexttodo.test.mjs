// Tests for the "Next to do" button in the rail (logic.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { nextToDo } from "../../rdgai/gui/static/logic.js";

test("ready once every pair in the unit is labelled: the button becomes the main action", () => {
  const unit = { next_unclassified: "Mark.1.3", progress: { pairs_done: 6, pairs_total: 6 } };
  assert.deepEqual(nextToDo(unit), { target: "Mark.1.3", ready: true });
});

test("not ready while pairs are still unlabelled (it is still there, to skip ahead)", () => {
  const unit = { next_unclassified: "Mark.1.3", progress: { pairs_done: 2, pairs_total: 6 } };
  assert.deepEqual(nextToDo(unit), { target: "Mark.1.3", ready: false });
});

test("nothing left to do: no target", () => {
  const unit = { next_unclassified: null, progress: { pairs_done: 6, pairs_total: 6 } };
  assert.deepEqual(nextToDo(unit), { target: null, ready: false });
});

test("a unit with no pairs to label (all by rule) counts as done", () => {
  const unit = { next_unclassified: "Mark.1.3", progress: { pairs_done: 0, pairs_total: 0 } };
  assert.deepEqual(nextToDo(unit), { target: "Mark.1.3", ready: true });
});

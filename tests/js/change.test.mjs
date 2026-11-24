// Tests for splitting a computed change into its description and its words (logic.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { changeParts } from "../../rdgai/gui/static/logic.js";

test("description and words", () => {
  assert.deepEqual(changeParts("second reading omits: και"), { label: "second reading omits", words: "και" });
  assert.deepEqual(changeParts("changed: εξηλθεν → εξηλθον"), { label: "changed", words: "εξηλθεν → εξηλθον" });
});

test("a change with no words", () => {
  assert.deepEqual(changeParts("same words, reordered"), { label: "same words, reordered", words: "" });
  assert.deepEqual(changeParts(""), { label: "", words: "" });
});

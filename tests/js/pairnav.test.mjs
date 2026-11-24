// Tests for the pair rail beside the pairs: one entry per pair shown, with where the annotator is up to (logic.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { pairNavItems } from "../../rdgai/gui/static/logic.js";

const pairs = [
  { first: "a", second: "b", category: "Minor_Omission" },
  { first: "a", second: "c", category: null },
  { first: "b", second: "c", category: "Orthography", source: "rule" },
];

test("one entry per pair, reading id to reading id, in display order", () => {
  assert.deepEqual(pairNavItems(pairs, 0).map((item) => item.label), ["a → b", "a → c", "b → c"]);
  assert.deepEqual(pairNavItems(pairs, 0).map((item) => item.index), [0, 1, 2]);
});

test("the pair the annotator is on is the current one", () => {
  assert.deepEqual(pairNavItems(pairs, 1).map((item) => item.current), [false, true, false]);
});

test("each entry says whether the pair is labelled, and whether by rule", () => {
  const items = pairNavItems(pairs, 0);
  assert.deepEqual(items.map((item) => item.done), [true, false, true]);
  assert.deepEqual(items.map((item) => item.rule), [false, false, true]);
  assert.equal(items[0].category, "Minor_Omission");
});

test("no pairs, no entries", () => {
  assert.deepEqual(pairNavItems([], 0), []);
});

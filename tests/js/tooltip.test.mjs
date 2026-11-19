// Tests for the category buttons' tooltips (logic.js): the name, key and inverse, never the full definition.
import { test } from "node:test";
import assert from "node:assert/strict";
import { categoryTitle } from "../../rdgai/gui/static/logic.js";

const omission = { name: "Minor_Omission", inverse: "Minor_Addition", key: "7", description: "A long definition of the category, several sentences long." };
const orthography = { name: "Orthography", inverse: "Orthography", key: "shift+1", description: "Spelling only." };

test("the tooltip names the category, its key and its inverse", () => {
  assert.equal(categoryTitle(omission), "Minor_Omission (key 7) · inverse: Minor_Addition");
});

test("a category that is its own inverse doesn't repeat its name", () => {
  assert.equal(categoryTitle(orthography), "Orthography (key ⇧1)");
});

test("no key, no key note", () => {
  assert.equal(categoryTitle({ ...omission, key: null }), "Minor_Omission · inverse: Minor_Addition");
});

test("the definition is never in the tooltip", () => {
  for (const button of [omission, orthography]) assert.ok(!categoryTitle(button).includes(button.description));
});

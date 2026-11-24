// Tests for reading the marks in a translation that show which words render the reading (logic.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { markedParts } from "../../rdgai/gui/static/logic.js";

test("the marked span is highlighted", () => {
  assert.deepEqual(markedParts("of Jesus ⸂Christ⸃, Son of God"), [
    { text: "of Jesus ", marked: false },
    { text: "Christ", marked: true },
    { text: ", Son of God", marked: false },
  ]);
});

test("several marked spans", () => {
  assert.deepEqual(markedParts("the ⸂Son⸃ of ⸂God⸃").filter((part) => part.marked).map((part) => part.text), ["Son", "God"]);
});

test("an omission is a marked sign", () => {
  assert.deepEqual(markedParts("of Jesus⸆, Son"), [
    { text: "of Jesus", marked: false },
    { text: "⸆", marked: true, omission: true },
    { text: ", Son", marked: false },
  ]);
});

test("a translation without marks has none", () => {
  assert.equal(markedParts("of Jesus Christ"), null);
  assert.equal(markedParts(""), null);
});

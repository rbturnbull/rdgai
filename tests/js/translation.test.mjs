// Tests for highlighting the words in which two translations differ (rdgai/gui/static/logic.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { diffWords } from "../../rdgai/gui/static/logic.js";

const changed = (parts) => parts.filter((part) => part.changed).map((part) => part.text);

test("an omitted word is marked in the first translation only", () => {
  const { first, second } = diffWords("The beginning of the gospel of Jesus Christ, Son of God.", "The beginning of the gospel of Jesus, Son of God.");
  assert.deepEqual(changed(first), ["Christ,"]);
  assert.deepEqual(changed(second), []);   // "Jesus" and "Jesus," differ only in punctuation
});

test("the parts join back into the original text", () => {
  const a = "And immediately he called them; and leaving their father";
  const b = "And he called them at once; and leaving their father";
  const { first, second } = diffWords(a, b);
  assert.equal(first.map((part) => part.text).join(""), a);
  assert.equal(second.map((part) => part.text).join(""), b);
});

test("a substitution is marked on both sides", () => {
  const { first, second } = diffWords("he said to them", "he cried to them");
  assert.deepEqual(changed(first), ["said"]);
  assert.deepEqual(changed(second), ["cried"]);
});

test("case alone is not a difference", () => {
  const { first, second } = diffWords("And he went", "and he went");
  assert.deepEqual(changed(first), []);
  assert.deepEqual(changed(second), []);
});

test("identical and missing translations", () => {
  assert.deepEqual(changed(diffWords("same words", "same words").first), []);
  assert.deepEqual(diffWords("", "x").first, []);
  assert.deepEqual(changed(diffWords("", "x").second), ["x"]);
});

import { keyAction } from "../../rdgai/gui/static/logic.js";

test("t shows or hides the translations, in every view", () => {
  const press = (view) => ({ key: "t", code: "KeyT", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, inTextField: false, view });
  assert.deepEqual(keyAction(press("unit"), []), { type: "translation" });
  assert.deepEqual(keyAction(press("category"), []), { type: "translation" });
  assert.equal(keyAction({ ...press("unit"), inTextField: true }, []), null);
});

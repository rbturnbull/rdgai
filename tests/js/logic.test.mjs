// Tests for the pure logic of the annotation GUI (rdgai/gui/static/logic.js). Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { keyAction, categoryClick, targetPairs, pairKey, expectedFor, labelRequest, certaintyRequest } from "../../rdgai/gui/static/logic.js";

const buttons = [
  { name: "Orthography", key: "1" },
  { name: "Transposition", key: "2" },
  { name: "Major_Omission", key: "0" },
  { name: "Eleventh", key: "shift+1" },
];

const press = (key, extra = {}) => ({ key, code: /^[0-9]$/.test(key) ? `Digit${key}` : `Key${key.toUpperCase()}`, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, inTextField: false, ...extra });

test("number keys select categories in button order", () => {
  assert.deepEqual(keyAction(press("1"), buttons), { type: "category", name: "Orthography" });
  assert.deepEqual(keyAction(press("0"), buttons), { type: "category", name: "Major_Omission" });
  assert.equal(keyAction(press("5"), buttons), null);
});

test("shift + number selects categories beyond ten (by key code, whatever the keyboard layout)", () => {
  assert.deepEqual(keyAction(press("!", { code: "Digit1", shiftKey: true }), buttons), { type: "category", name: "Eleventh" });
});

test("j/k move between pairs, h/m/l set certainty, n goes to the next unclassified unit, x ticks", () => {
  assert.deepEqual(keyAction(press("j"), buttons), { type: "move", delta: 1 });
  assert.deepEqual(keyAction(press("k"), buttons), { type: "move", delta: -1 });
  assert.deepEqual(keyAction(press("h"), buttons), { type: "certainty", value: "high" });
  assert.deepEqual(keyAction(press("m"), buttons), { type: "certainty", value: "medium" });
  assert.deepEqual(keyAction(press("l"), buttons), { type: "certainty", value: "low" });
  assert.deepEqual(keyAction(press("n"), buttons), { type: "next_unclassified" });
  assert.deepEqual(keyAction(press("x"), buttons), { type: "toggle_tick" });
  assert.deepEqual(keyAction(press("?", { code: "Slash", shiftKey: true }), buttons), { type: "help" });
});

test("keys are ignored while typing a reason, except Escape which leaves the text box", () => {
  assert.equal(keyAction(press("j", { inTextField: true }), buttons), null);
  assert.equal(keyAction(press("1", { inTextField: true }), buttons), null);
  assert.deepEqual(keyAction(press("Escape", { code: "Escape", inTextField: true }), buttons), { type: "leave_text" });
});

test("keys with modifiers are left to the browser", () => {
  assert.equal(keyAction(press("1", { metaKey: true }), buttons), null);
  assert.equal(keyAction(press("j", { ctrlKey: true }), buttons), null);
});

test("clicking the current category clears it; another category replaces it", () => {
  assert.deepEqual(categoryClick("Minor_Omission", "Minor_Omission"), { category: null });
  assert.deepEqual(categoryClick("Minor_Omission", "Major_Omission"), { category: "Major_Omission" });
  assert.deepEqual(categoryClick(null, "Major_Omission"), { category: "Major_Omission" });
});

const pairs = [
  { first: "a", second: "b", category: null },
  { first: "a", second: "c", category: "Minor_Omission" },
  { first: "b", second: "c", category: null },
];

test("actions apply to ticked pairs (in display order) or else the focused pair", () => {
  assert.deepEqual(targetPairs(pairs, 1, new Set()), [pairs[1]]);
  assert.deepEqual(targetPairs(pairs, 0, new Set(["b|c", "a|b"])), [pairs[0], pairs[2]]);
  assert.deepEqual(targetPairs(pairs, -1, new Set()), []);
});

test("requests carry the categories the page last saw, so stale writes are refused", () => {
  assert.equal(pairKey(pairs[1]), "a|c");
  assert.deepEqual(expectedFor([pairs[0], pairs[1]]), { "a|b": null, "a|c": "Minor_Omission" });
  assert.deepEqual(labelRequest("Mark.1.1.10", [pairs[0]], "Major_Omission", "medium", "reason"), {
    unit_id: "Mark.1.1.10",
    pairs: [["a", "b"]],
    category: "Major_Omission",
    cert: "medium",
    justification: "reason",
    expected: { "a|b": null },
  });
  assert.deepEqual(certaintyRequest("Mark.1.1.10", [pairs[1]], "low"), { unit_id: "Mark.1.1.10", pairs: [["a", "c"]], cert: "low" });
});

import { visiblePairs, progressText, ruleText } from "../../rdgai/gui/static/logic.js";

test("rule pairs are only navigable when their section is expanded", () => {
  const unit = { pairs: [{ first: "a", second: "b" }], rule_pairs: [{ first: "a", second: "ao" }] };
  assert.deepEqual(visiblePairs(unit, false), [unit.pairs[0]]);
  assert.deepEqual(visiblePairs(unit, true), [unit.pairs[0], unit.rule_pairs[0]]);
  assert.deepEqual(visiblePairs({ pairs: [] }, true), []);
});

test("progress shows the pairs to annotate and those labelled by rule", () => {
  assert.equal(progressText({ pairs_done: 1, pairs_total: 3, rule_pairs: 12 }), "1 / 3 pairs · 12 by rule");
  assert.equal(progressText({ pairs_done: 0, pairs_total: 15, rule_pairs: 0 }), "0 / 15 pairs");
});

test("rule pairs explain where their label comes from", () => {
  assert.equal(ruleText({ rule: 1 }), "By rule: subreading and parent");
  assert.equal(ruleText({ rule: 2 }), "By rule: subreadings of the same reading");

});

import { refreshAction } from "../../rdgai/gui/static/logic.js";

test("the page reloads the unit when the file has changed, unless hidden or while typing", () => {
  assert.equal(refreshAction({ hidden: false, typing: false, known: "v1", latest: "v1" }), "none");
  assert.equal(refreshAction({ hidden: false, typing: false, known: "v1", latest: "v2" }), "reload");
  assert.equal(refreshAction({ hidden: true, typing: false, known: "v1", latest: "v2" }), "skip");
  // never replace a reason box while it is being typed in; reload once typing stops
  assert.equal(refreshAction({ hidden: false, typing: true, known: "v1", latest: "v2" }), "later");
  // no version known yet (first check): just remember it
  assert.equal(refreshAction({ hidden: false, typing: false, known: null, latest: "v1" }), "remember");
});

import { justificationSource, displayJustification } from "../../rdgai/gui/static/logic.js";

test("a reason counts as copied only if it is saved exactly as copied", () => {
  const copied = { text: "omits the article", source: "claude" };
  assert.equal(justificationSource("omits the article", copied), "claude");
  assert.equal(justificationSource("  omits the article ", copied), "claude");
  assert.equal(justificationSource("omits the article του", copied), null);  // edited: the annotator's own
  assert.equal(justificationSource("", copied), null);
  assert.equal(justificationSource("omits the article", undefined), null);
});

test("label requests say whose wording a copied reason is", () => {
  const pair = { first: "a", second: "b", category: null };
  assert.equal(labelRequest("U", [pair], "X", "high", "r").justification_source, undefined);
  assert.equal(labelRequest("U", [pair], "X", "high", "r", "claude").justification_source, "claude");
});

test("the 'Inverse of a → b:' prefix is not shown", () => {
  assert.equal(displayJustification("Inverse of b → a: adds του"), "adds του");
  assert.equal(displayJustification("adds του"), "adds του");
  assert.equal(displayJustification(""), "");
});

import { isCompact, groupByFirst, sameSuggestion, sameChange } from "../../rdgai/gui/static/logic.js";

const many = (count) => Array.from({ length: count }, (_, index) => ({ first: "a", second: `r${index}` }));

test("units with more pairs than the threshold are shown compactly", () => {
  assert.equal(isCompact({ pairs: many(10) }, 10), false);
  assert.equal(isCompact({ pairs: many(11) }, 10), true);
  assert.equal(isCompact({ pairs: many(3), rule_pairs: many(9) }, 10), true);  // rule pairs count towards the length
});

test("compact rows are grouped by their first reading, in display order", () => {
  const pairs = [
    { first: "a", second: "b" }, { first: "a", second: "c" }, { first: "b", second: "c" }, { first: "a", second: "d" },
  ];
  assert.deepEqual(groupByFirst(pairs).map((group) => [group.first, group.pairs.map((pair) => pair.second)]),
    [["a", ["b", "c"]], ["b", ["c"]], ["a", ["d"]]]);
});

const suggested = [
  { first: "a", second: "b", suggestions: [{ category: "Minor_Omission" }], change: { text: "second reading omits: του" } },
  { first: "a", second: "c", suggestions: [{ category: "Minor_Omission" }], change: { text: "second reading omits: θεου" } },
  { first: "a", second: "d", suggestions: [{ category: "Major_Omission" }], change: { text: "second reading omits: του" } },
  { first: "a", second: "e", suggestions: [], change: { text: "second reading omits: του" } },
];

test("tick all pairs with the same suggestion as the focused pair", () => {
  assert.deepEqual(sameSuggestion(suggested, suggested[0]), ["a|b", "a|c"]);
  assert.deepEqual(sameSuggestion(suggested, suggested[3]), []);  // no suggestion: nothing to match
});

test("tick all pairs with the same change as the focused pair", () => {
  assert.deepEqual(sameChange(suggested, suggested[0]), ["a|b", "a|d", "a|e"]);
  assert.deepEqual(sameChange(suggested, { first: "x", second: "y" }), []);
});

test("Enter or e expands the focused row; other keys are unchanged in compact mode", () => {
  assert.deepEqual(keyAction(press("Enter", { code: "Enter" }), buttons), { type: "expand" });
  assert.deepEqual(keyAction(press("e"), buttons), { type: "expand" });
  assert.deepEqual(keyAction(press("1"), buttons), { type: "category", name: "Orthography" });
  assert.deepEqual(keyAction(press("j"), buttons), { type: "move", delta: 1 });
});

test("progress uses the singular for one pair", () => {
  assert.equal(progressText({ pairs_done: 0, pairs_total: 1, rule_pairs: 0 }), "0 / 1 pair");
  assert.equal(progressText({ pairs_done: 1, pairs_total: 1, rule_pairs: 1 }), "1 / 1 pair · 1 by rule");
  assert.equal(progressText({ pairs_done: 0, pairs_total: 2, rule_pairs: 0 }), "0 / 2 pairs");
  assert.equal(progressText({ pairs_done: 0, pairs_total: 0, rule_pairs: 3 }), "0 / 0 pairs · 3 by rule");
});

import { countOf } from "../../rdgai/gui/static/logic.js";

test("counts use the singular for one", () => {
  assert.equal(countOf(1, "pair"), "1 pair");
  assert.equal(countOf(0, "pair"), "0 pairs");
  assert.equal(countOf(3, "pair"), "3 pairs");
  assert.equal(countOf(1, "witness", "witnesses"), "1 witness");
  assert.equal(countOf(2, "witness", "witnesses"), "2 witnesses");
});

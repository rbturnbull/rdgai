// Tests for the counts on "Save all suggestions" and "Save all reasons" (logic.js): the same rules as the server.
import { test } from "node:test";
import assert from "node:assert/strict";
import { suggestionCounts } from "../../rdgai/gui/static/logic.js";

const suggestion = (category, justification = "") => ({ category, justification });
const pair = (fields) => ({ first: "a", second: "b", category: null, source: null, reason: "", rule: null, suggestions: [], ...fields });

test("labels: unlabelled pairs whose suggestions agree", () => {
  const pairs = [
    pair({ suggestions: [suggestion("Minor_Omission", "x")] }),
    pair({ suggestions: [suggestion("Minor_Omission"), suggestion("Minor_Omission", "y")] }),   // agree
    pair({ suggestions: [suggestion("Minor_Omission"), suggestion("Major_Omission")] }),        // disagree
    pair({ category: "Major_Omission", source: "human", suggestions: [suggestion("Minor_Omission")] }),   // labelled
    pair({ rule: { rule: 1 }, suggestions: [suggestion("Orthography")] }),                       // by rule
    pair({}),                                                                                     // no suggestion
  ];
  assert.equal(suggestionCounts(pairs).labels, 2);
});

test("reasons: the annotator's labels without a reason, with a suggested reason for the same category", () => {
  const pairs = [
    pair({ category: "Minor_Omission", source: "human", suggestions: [suggestion("Minor_Omission", "omits του")] }),
    pair({ category: "Minor_Omission", source: "human", reason: "mine", suggestions: [suggestion("Minor_Omission", "omits")] }),
    pair({ category: "Major_Omission", source: "human", suggestions: [suggestion("Minor_Omission", "omits")] }),
    pair({ category: "Minor_Omission", source: "human", suggestions: [suggestion("Minor_Omission")] }),        // no reason
    pair({ category: "Minor_Omission", source: "pipeline", suggestions: [suggestion("Minor_Omission", "x")] }),
    pair({ suggestions: [suggestion("Minor_Omission", "x")] }),                                            // unlabelled
  ];
  assert.equal(suggestionCounts(pairs).reasons, 1);
});

test("no pairs, nothing to save", () => {
  assert.deepEqual(suggestionCounts([]), { labels: 0, reasons: 0 });
});

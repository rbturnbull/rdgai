// Tests for the sample progress bars in the sidebar (logic.js): which rows show, and their text.
import { test } from "node:test";
import assert from "node:assert/strict";
import { sampleProgressRows } from "../../rdgai/gui/static/logic.js";

const progress = {
  sample: true,
  example: { pairs_done: 181, pairs_total: 576, units_done: 12, units_total: 100 },
  test: { pairs_done: 0, pairs_total: 735, units_done: 0, units_total: 100 },
  file: { pairs_done: 628, pairs_total: 44006, units_done: 113, units_total: 5626 },
};

test("the whole sample selected: example, test, and the whole file", () => {
  const rows = sampleProgressRows(progress, "sampled");
  assert.deepEqual(rows.map((row) => row.role), ["example", "test", "all"]);
  assert.deepEqual(rows[0], {
    role: "example", heading: "Example pairs (181/576)", percent: 31.4, percentText: "31.4%",
    units: "Example units 12/100 (12.0%)",
  });
  assert.equal(rows[1].heading, "Test pairs (0/735)");
  assert.equal(rows[1].percentText, "0.0%");
  assert.deepEqual(rows[2], {
    role: "all", heading: "All pairs (628/44006)", percent: 1.4, percentText: "1.4%",
    units: "All units 113/5626 (2.0%)",
  });
});

test("one role selected: that one, and the whole file", () => {
  assert.deepEqual(sampleProgressRows(progress, "example").map((row) => row.role), ["example", "all"]);
  assert.deepEqual(sampleProgressRows(progress, "test").map((row) => row.role), ["test", "all"]);
});

test("all units in the file: the whole file only", () => {
  assert.deepEqual(sampleProgressRows(progress, "all").map((row) => row.role), ["all"]);
  assert.deepEqual(sampleProgressRows({ sample: false, file: progress.file }, "all").map((row) => row.role), ["all"]);
});

test("no sample: no sample bars, and nothing without the whole file's totals", () => {
  assert.deepEqual(sampleProgressRows({ sample: false, file: progress.file }, "sampled").map((row) => row.role), ["all"]);
  assert.deepEqual(sampleProgressRows({ sample: false }, "sampled"), []);
  assert.deepEqual(sampleProgressRows(null, "sampled"), []);
});

test("one decimal place, rounded; nothing to label is 100%", () => {
  const rows = sampleProgressRows({ sample: true, example: { pairs_done: 2, pairs_total: 3, units_done: 1, units_total: 3 } }, "example");
  assert.equal(rows[0].percentText, "66.7%");
  assert.equal(rows[0].units, "Example units 1/3 (33.3%)");
  const empty = sampleProgressRows({ sample: true, example: { pairs_done: 0, pairs_total: 0, units_done: 0, units_total: 0 } }, "example");
  assert.equal(empty[0].percentText, "100.0%");
});

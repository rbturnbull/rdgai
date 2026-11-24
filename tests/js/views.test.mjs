// Tests for the routing and paging logic of the GUI's views (rdgai/gui/static/logic.js). Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoute, routeHash, QUEUE_FILTERS, pageInfo, keyAction } from "../../rdgai/gui/static/logic.js";

test("a unit route keeps its queue filters", () => {
  const route = parseRoute("#/unit/Mark.1.1.12-16?sample=all&category=Major_Omission&disagreement=1");
  assert.equal(route.view, "unit");
  assert.equal(route.unitId, "Mark.1.1.12-16");
  assert.deepEqual(route.filters, { sample: "all", status: "", verse: "", category: "Major_Omission", cert: "", source: "", disagreement: "1" });
});

test("unit ids are decoded", () => {
  assert.equal(parseRoute("#/unit/Mark.1.1%2F2").unitId, "Mark.1.1/2");
});

test("the category view", () => {
  const route = parseRoute("#/category/Major_Omission?source=human&offset=100");
  assert.equal(route.view, "category");
  assert.equal(route.category, "Major_Omission");
  assert.deepEqual(route.params, { source: "human", offset: "100" });
});

test("the agreement view", () => {
  const route = parseRoute("#/agreement?scope=sampled&source=claude");
  assert.equal(route.view, "agreement");
  assert.deepEqual(route.params, { scope: "sampled", source: "claude" });
});

test("anything else starts the queue", () => {
  for (const hash of ["", "#", "#/", "#/?sample=test"]) assert.equal(parseRoute(hash).view, "queue");
  assert.equal(parseRoute("#/?sample=test").filters.sample, "test");
});

test("routes round-trip, leaving out empty parameters", () => {
  assert.equal(routeHash({ view: "unit", unitId: "Mark.1.1.10", filters: { sample: "all", status: "" } }), "#/unit/Mark.1.1.10?sample=all");
  assert.equal(routeHash({ view: "category", category: "Minor_Omission", params: { source: "", offset: 0 } }), "#/category/Minor_Omission");
  assert.equal(routeHash({ view: "agreement", params: { scope: "sampled" } }), "#/agreement?scope=sampled");
  assert.equal(routeHash({ view: "queue", filters: {} }), "#/");
  const hash = routeHash({ view: "unit", unitId: "Mark.1.2.2", filters: { cert: "low", disagreement: "1" } });
  assert.deepEqual(parseRoute(hash).filters, { ...Object.fromEntries(QUEUE_FILTERS.map((name) => [name, ""])), cert: "low", disagreement: "1" });
});

test("paging", () => {
  assert.deepEqual(pageInfo(250, 100, 100), { from: 101, to: 200, prev: 0, next: 200 });
  assert.deepEqual(pageInfo(250, 0, 100), { from: 1, to: 100, prev: null, next: 100 });
  assert.deepEqual(pageInfo(250, 200, 100), { from: 201, to: 250, prev: 100, next: null });
  assert.deepEqual(pageInfo(0, 0, 100), { from: 0, to: 0, prev: null, next: null });
});

test("annotation keys are off outside the unit view, but help still works", () => {
  const press = (key) => ({ key, code: `Key${key.toUpperCase()}`, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, inTextField: false });
  assert.equal(keyAction({ ...press("1"), code: "Digit1", view: "category" }, [{ name: "A", key: "1" }]), null);
  assert.equal(keyAction({ ...press("j"), view: "agreement" }, []), null);
  assert.deepEqual(keyAction({ ...press("?"), view: "category" }, []), { type: "help" });
  assert.deepEqual(keyAction({ ...press("j"), view: "unit" }, []), { type: "move", delta: 1 });
});

import { showsOverview } from "../../rdgai/gui/static/logic.js";

test("the verse and readings overview is shown only for compact units (whose rows do not show them)", () => {
  const unit = (pairs) => ({ pairs: Array.from({ length: pairs }, (_, index) => ({ first: "a", second: String(index) })), rule_pairs: [] });
  assert.equal(showsOverview(unit(3), 10), false);
  assert.equal(showsOverview(unit(11), 10), true);
});

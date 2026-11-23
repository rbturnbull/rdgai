// The rdgai annotation GUI. Talks to the JSON API in rdgai/gui/__init__.py; pure logic lives in logic.js.
import { keyAction, categoryClick, targetPairs, pairKey, labelRequest, certaintyRequest, visiblePairs, progressText, ruleText, refreshAction, justificationSource,
  isCompact, groupByFirst, sameSuggestion, sameChange, parseRoute, routeHash, QUEUE_FILTERS, pageInfo, expectedFor, diffWords, markedParts, showsOverview, headerUser, changeParts, countOf, canUseReason, usableReason, pairNavItems, nextToDo, categoryTitle, suggestionCounts, sampleProgressRows } from "./logic.js";

const state = {
  config: null,
  unit: null,
  focused: 0,
  ticked: new Set(),
  pendingCert: {},   // pairKey -> certainty chosen before a category (unlabelled pairs)
  reasons: {},       // pairKey -> edited reason text not yet saved
  copied: {},        // pairKey -> {text, source} when a suggestion's reason was copied into the reason box
  expanded: new Set(), // in compact mode: pairKeys of the rows shown as full panels
  filters: {},
  showRules: false,  // whether the section of pairs labelled by rule is expanded
  version: null,     // version token of the file the shown view was read from
  view: "unit",      // "unit", "category" or "agreement"
  lastUnitId: null,  // the unit to go back to from the other views
  category: null,    // the category view: {params, data, counts}
  agreement: null,   // the agreement view: {params, data}
  editing: null,     // the definition being edited in the definitions panel: {kind, name}
  look: readPreference("rdgai.look", "cards"),  // how readings are shown: "cards" (the original rdgai look) or "table"
  showTranslation: readPreference("rdgai.translation", "on") === "on",  // contextual translations under each pair
};

function readPreference(name, fallback) {
  try { return localStorage.getItem(name) || fallback; } catch { return fallback; }
}

function savePreference(name, value) {
  try { localStorage.setItem(name, value); } catch { /* kept for this page only */ }
}

function toggleTranslation() {
  state.showTranslation = !state.showTranslation;
  savePreference("rdgai.translation", state.showTranslation ? "on" : "off");
  syncTranslationToggle();
  if (state.view === "unit") render();
  else if (state.view === "category" && state.category) renderCategory();
}

function syncTranslationToggle() {
  $("translation-toggle").checked = state.showTranslation;
}

const stripMarks = (text) => (text || "").replace(/[⸂⸃⸆]/g, "");

// A translation with the words that render the reading highlighted: the span Claude marked ⸂…⸃ (⸆ for an omission),
// or, in a translation without marks, the words that differ from the other reading's translation.
function translationParts(own, other, ownIsFirst) {
  if (!own) return [el("span", { class: "muted" }, "Not translated yet.")];
  const marked = markedParts(own);
  if (marked) return marked.map((part) => (part.marked ? el("mark", { class: part.omission ? "omission" : null }, part.text) : part.text));
  if (!other) return [own];
  const { first, second } = ownIsFirst ? diffWords(own, stripMarks(other)) : diffWords(stripMarks(other), own);
  return (ownIsFirst ? first : second).map((part) => (part.changed ? el("mark", {}, part.text) : part.text));
}

// The verse translated with each reading of the pair in place (for the By category view).
function translationBlock(pair) {
  if (!state.showTranslation || (!pair.first_translation && !pair.second_translation)) return null;
  const line = (id, own, other, isFirst) => el("div", { class: "translation-line" },
    el("span", { class: "ids" }, id), el("span", {}, translationParts(own, other, isFirst)));
  return el("div", { class: "translation", title: "The verse translated with each reading in place (by Claude)" },
    line(pair.first, pair.first_translation, pair.second_translation, true),
    line(pair.second, pair.second_translation, pair.first_translation, false));
}

// One reading of a pair: its card, then the verse with the reading in place, its translation and its witnesses.
function pairReadingRow(pair, which) {
  const isFirst = which === "first";
  const id = isFirst ? pair.first : pair.second;
  const text = isFirst ? pair.first_text : pair.second_text;
  const reading = state.unit?.readings.find((candidate) => candidate.id === id);
  const omitted = !reading?.text || text === "OMIT";
  const { before, after } = state.unit.context;
  const own = isFirst ? pair.first_translation : pair.second_translation;
  const other = isFirst ? pair.second_translation : pair.first_translation;
  return el("div", { class: "pair-reading" },
    el("div", { class: `reading-card${reading?.excluded ? " excluded" : ""}` },
      el("div", { class: "reading-id" }, id, reading?.type ? ` · ${reading.type}` : ""),
      el("div", { class: "reading-text" }, text, ...(reading ? renderNotes(reading.notes) : []))),
    el("div", { class: "reading-detail" },
      el("p", { class: "greek" }, before ? `${before} ` : "",
        el("mark", { class: omitted ? "omission" : null, title: omitted ? "This reading omits the words" : null }, omitted ? "⸆" : text),
        after ? ` ${after}` : ""),
      state.showTranslation ? el("p", { class: "english" }, translationParts(own, other, isFirst)) : null,
      reading ? el("details", { class: "witnesses" },
        el("summary", {}, `${reading.witness_count} witness${reading.witness_count === 1 ? "" : "es"}`),
        el("div", {}, reading.witnesses.join(" "))) : null),
  );
}

// The computed change between the two readings, as a badge: what changes, then the words in bold.
function changeBadge(text) {
  const { label, words } = changeParts(text);
  return el("div", { class: "change-badge", title: "Computed from the words of the two readings; a reading aid, not a label" },
    el("span", { class: "change-label" }, label),
    words ? el("span", { class: "change-words" }, words) : null);
}

function pairReadings(pair) {
  return el("div", { class: "pair-readings" },
    pairReadingRow(pair, "first"),
    el("div", { class: "pair-change" },
      el("div", { class: "down-arrow", "aria-hidden": "true" }, "↓"),
      pair.change ? changeBadge(pair.change.text) : null),
    pairReadingRow(pair, "second"));
}

function setLook(look) {
  state.look = look;
  savePreference("rdgai.look", look);
  document.body.classList.toggle("look-cards", look === "cards");
  if (state.unit && !$("readings").hidden) renderReadings(state.unit);
}

const POLL_MILLISECONDS = 3000;

const visible = () => (state.unit ? visiblePairs(state.unit, state.showRules) : []);

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "style") Object.assign(node.style, value);
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (value === true) node.setAttribute(name, "");
    else node.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// ---------------------------------------------------------------------------
// API and routing
// ---------------------------------------------------------------------------

async function api(path, body) {
  const options = body === undefined
    ? { cache: "no-store" }
    : { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(data.error || response.statusText), { status: response.status, data });
  return data;
}

function filterQuery(filters = state.filters) {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(filters)) if (value) params.set(name, value);
  const query = params.toString();
  return query ? `?${query}` : "";
}

function unitHash(unitId, filters = state.filters) {
  return routeHash({ view: "unit", unitId, filters });
}

function goToUnit(unitId) {
  if (!unitId) return;
  const target = unitHash(unitId);
  if (location.hash === target) loadUnit(unitId);
  else location.hash = target;
}

async function route() {
  const current = parseRoute(location.hash);
  showView(current.view === "queue" ? "unit" : current.view);
  if (current.view === "category") return loadCategory(current.category, current.params);
  if (current.view === "agreement") return loadAgreement(current.params);
  state.filters = current.filters;
  if (current.view === "unit") return loadUnit(current.unitId);
  return goToQueueStart();
}

function showView(view) {
  state.view = view;
  $("view-unit").hidden = view !== "unit";
  $("view-category").hidden = view !== "category";
  $("view-agreement").hidden = view !== "agreement";
  $("queue-filters").hidden = view !== "unit";
  if (view !== "unit") {
    $("header-unit").replaceChildren(el("div", { class: "unit-title" }, el("span", { class: "view-title" }, view === "category" ? "By category" : "Agreement")));
  } else if (state.unit) {
    renderHeader(state.unit);
  }
  for (const button of document.querySelectorAll("[data-view]")) {
    button.classList.toggle("active", button.dataset.view === (view === "unit" ? "annotate" : view));
  }
  $("main").classList.toggle("wide", view !== "unit" || compactMode());
}

function openView(view) {
  if (view === "annotate") {
    location.hash = state.lastUnitId ? unitHash(state.lastUnitId) : routeHash({ view: "queue", filters: state.filters });
  } else if (view === "category") {
    const name = state.category?.data.category || state.config.buttons[0]?.name;
    if (name) location.hash = routeHash({ view: "category", category: name, params: {} });
  } else {
    location.hash = routeHash({ view: "agreement", params: state.agreement?.params || {} });
  }
}

async function goToQueueStart() {
  const queue = await api(`/api/queue${filterQuery()}`);
  state.filters = queue.filters;
  syncFilterControls();
  showQueueCount(queue.total);
  if (!queue.units.length) {
    state.unit = null;
    $("header-unit").replaceChildren(el("span", { class: "muted" }, "No units match these filters."));
    for (const id of ["context", "readings", "pairs"]) $(id).replaceChildren();
    delete $("readings").dataset.unit;
    $("bulk-bar").hidden = true;
    return;
  }
  const first = queue.units.find((unit) => unit.status === "unclassified" || unit.status === "partial") || queue.units[0];
  location.replace(unitHash(first.id));
  await loadUnit(first.id);
}

// Loads can overlap (Next, the reload after a save, the check for changes): only the newest one may change the page,
// so a slow reload of the previous unit can't put it back after Next. Reloads are for the unit being opened.
let latestLoad = 0;
let wantedUnitId = null;

function reloadUnit() {
  return loadUnit(wantedUnitId || state.unit.id, { keepFocus: true });
}

async function loadUnit(unitId, { keepFocus = false } = {}) {
  const load = ++latestLoad;
  wantedUnitId = unitId;
  try {
    const unit = await api(`/api/unit/${encodeURIComponent(unitId)}${filterQuery()}`);
    if (load !== latestLoad) return;   // a newer load has started: it decides what is shown
    const previousId = state.unit?.id;
    state.unit = unit;
    state.lastUnitId = unit.id;
    // tell the server which unit is open, so Claude can act on "the verse I'm on" (get_current_unit)
    if (previousId !== unit.id) api("/api/viewing", { unit_id: unit.id }).catch(() => {});
    state.version = unit.version;
    state.filters = unit.filters;
    if (!keepFocus || previousId !== unit.id) {
      state.focused = 0;
      state.ticked.clear();
      state.pendingCert = {};
      state.reasons = {};
      state.copied = {};
      state.expanded = new Set();
      state.showRules = false;
    }
    state.focused = Math.min(state.focused, Math.max(visible().length - 1, 0));
    syncFilterControls();
    showQueueCount(unit.position ? unit.position.total : null);
    const scroll = window.scrollY;
    render();
    window.scrollTo({ top: keepFocus && previousId === unit.id ? scroll : 0 });
  } catch (error) {
    if (load === latestLoad) showStatus(error.message, true);
  }
}

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

function render() {
  const unit = state.unit;
  if (!unit) return;
  renderHeader(unit);
  // the pair rows show the verse and the readings; the overview is only needed for compact rows
  const overview = showsOverview(unit, state.config.compact_threshold);
  $("context").hidden = !overview;
  $("readings").hidden = !overview;
  if (overview) {
    renderContext(unit);
    renderReadings(unit);
    $("readings").dataset.unit = unit.id;   // the cards always belong to the unit in the header
  } else {
    // never leave another unit's verse or reading cards behind, even if hidden
    $("context").replaceChildren();
    $("readings").replaceChildren();
    delete $("readings").dataset.unit;
  }
  renderBulkBar();
  loadSampleProgress();
  renderPairs(unit);
  renderPairNav(unit);
}

// the rail beside the pairs: the unit's progress, and one entry per pair with the current one highlighted
// The rail's elements are built once and then updated in place: a redraw (after a save, or the check for changes) must
// never replace a button between the press and the release of a click, or the click is lost.
function renderPairNav(unit) {
  const rail = $("pair-nav");
  if (!rail.querySelector(".unit-nav")) {
    const button = (className, onclick) => el("button", { type: "button", class: className, onclick });
    rail.replaceChildren(
      el("div", { class: "unit-nav" },
        el("div", { class: "unit-nav-row" },
          button("unit-prev", () => goToUnit(state.unit?.prev)),
          button("unit-next", () => goToUnit(state.unit?.next))),
        button("next-to-do", () => goToUnit(state.unit && nextToDo(state.unit).target))),
      el("div", { class: "rail-section rail-suggestions" },
        el("div", { class: "rail-heading" }, "Suggestions"),
        el("div", { class: "rail-note" }),
        button("save-labels", () => saveSuggestions("labels")),
        button("save-reasons", () => saveSuggestions("reasons"))),
      el("div", { class: "rail-section rail-select" },
        el("div", { class: "rail-heading", title: "Select all pairs like the current one, to label them together" }, "Select pairs like this one"),
        button("select-same-suggestion", (event) => selectKeys(event.currentTarget.dataset.tickKeys)),
        button("select-same-change", (event) => selectKeys(event.currentTarget.dataset.tickKeys))),
      el("div", { class: "pair-nav-progress" }),
      el("ol", { class: "pair-nav-list" }),
    );
  }
  const todo = nextToDo(unit);
  setButton(rail.querySelector(".unit-prev"), { text: "← Prev", disabled: !unit.prev,
    title: unit.prev ? `Previous unit: ${unit.prev}` : "This is the first unit" });
  setButton(rail.querySelector(".unit-next"), { text: "Next →", disabled: !unit.next,
    title: unit.next ? `Next unit: ${unit.next}` : "This is the last unit" });
  const next = rail.querySelector(".next-to-do");
  setButton(next, { text: todo.target ? "Next to do →" : "All done ✓", disabled: !todo.target,
    title: todo.target ? `The next unit with pairs to label: ${todo.target} (n)` : "No units left to label" });
  next.classList.toggle("ready", todo.ready);
  if (todo.target) next.dataset.target = todo.target;
  else delete next.dataset.target;
  rail.querySelector(".pair-nav-progress").textContent = progressText(unit.progress);
  updateRailSuggestions();
  updateRailSelect();

  const items = pairNavItems(visible(), state.focused);
  const list = rail.querySelector(".pair-nav-list");
  const signature = `${unit.id}|${items.map((item) => item.label).join(",")}`;
  if (list.dataset.signature !== signature) {
    list.dataset.signature = signature;
    list.replaceChildren(...items.map((item) => el("li", {}, el("button", { type: "button", class: "pair-nav-item", "data-nav-index": String(item.index) },
      el("span", { class: "pair-nav-mark", "aria-hidden": "true" }), el("span", { class: "pair-nav-ids" }, item.label)))));
  }
  list.querySelectorAll(".pair-nav-item").forEach((entry, index) => {
    const item = items[index];
    entry.classList.toggle("done", item.done);
    entry.classList.toggle("rule", item.rule);
    entry.querySelector(".pair-nav-mark").textContent = item.done ? "✓" : "";
    entry.title = item.category ? `${item.label}: ${item.category}${item.rule ? " (by rule)" : ""}` : `${item.label}: not labelled yet`;
  });
  updatePairNav();
}

function setButton(button, { text, disabled, title }) {
  if (button.textContent !== text) button.textContent = text;
  button.disabled = disabled;
  button.title = title;
}

function updatePairNav() {
  for (const item of document.querySelectorAll("#pair-nav .pair-nav-item")) {
    const current = Number(item.dataset.navIndex) === state.focused;
    item.classList.toggle("current", current);
    if (current) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");
  }
  // keep the current entry visible when the list is longer than the window, scrolling the rail only (never the page)
  const rail = $("pair-nav"), current = rail.querySelector(".pair-nav-item.current");
  if (current) {
    const top = current.getBoundingClientRect().top - rail.getBoundingClientRect().top + rail.scrollTop;
    const bottom = top + current.offsetHeight;
    if (top < rail.scrollTop) rail.scrollTop = top;
    else if (bottom > rail.scrollTop + rail.clientHeight) rail.scrollTop = bottom - rail.clientHeight;
  }
}

function renderHeader(unit) {
  const position = unit.position ? `${unit.position.index + 1} of ${unit.position.total}` : "not in this queue";
  $("header-unit").replaceChildren(
    el("div", { class: "unit-title" },
      el("h1", {}, unit.id),
      unit.sample_role ? el("span", { class: "badge" }, unit.sample_role) : null,
    ),
    el("div", { class: "unit-progress" }, el("span", {}, position)),
  );
}

function renderContext(unit) {
  const { before, lemma, after } = unit.context;
  $("context").replaceChildren(
    before ? `${before} ` : "",
    el("span", { class: "unit-text", title: "The variation unit" }, lemma || "⸆"),
    after ? ` ${after}` : "",
  );
}

function renderNotes(notes) {
  return notes.map((note) => {
    if (!note.references.length) return el("span", { class: "note" }, `(${note.text})`);
    const parts = [];
    note.references.forEach((reference, index) => {
      if (index) parts.push(", ");
      parts.push(reference.unit_id
        ? el("a", { href: unitHash(reference.unit_id), title: `${reference.unit_id}${reference.reading ? ` reading ${reference.reading}` : ""}` }, reference.label)
        : reference.label);
    });
    return el("span", { class: "note", title: note.text }, "(cf. ", ...parts, ")");
  });
}

function readingCard(reading) {
  return el("div", { class: `reading-card${reading.excluded ? " excluded" : ""}`, title: reading.excluded ? "Not classified (excluded reading type)" : null },
    el("div", { class: "reading-id" }, reading.id, reading.type ? ` · ${reading.type}` : ""),
    el("div", { class: "reading-text" }, reading.text, ...renderNotes(reading.notes)),
    el("details", { class: "reading-witnesses" },
      el("summary", {}, `${reading.witness_count} witness${reading.witness_count === 1 ? "" : "es"}`),
      el("div", {}, reading.witnesses.join(" "))),
  );
}

function renderReadings(unit) {
  if (state.look === "cards") {
    $("readings").replaceChildren(el("div", { class: "reading-cards" }, unit.readings.map(readingCard)));
    return;
  }
  const rows = unit.readings.map((reading) => el("tr", { class: reading.excluded ? "excluded" : "" },
    el("td", {}, reading.id),
    el("td", { class: "text" }, reading.text, ...renderNotes(reading.notes)),
    el("td", {}, reading.type || ""),
    el("td", {}, String(reading.witness_count)),
    el("td", {}, el("details", {}, el("summary", {}, "witnesses"), el("div", { class: "witnesses" }, reading.witnesses.join(" ")))),
  ));
  $("readings").replaceChildren(el("table", {},
    el("thead", {}, el("tr", {}, ...["", "Reading", "Type", "Witnesses", ""].map((heading) => el("th", {}, heading)))),
    el("tbody", {}, rows),
  ));
}

function renderBulkBar() {
  const bar = $("bulk-bar");
  bar.hidden = state.ticked.size === 0;
  if (bar.hidden) return;
  bar.replaceChildren(
    `${countOf(state.ticked.size, "pair")} selected: categories and certainty apply to ${state.ticked.size === 1 ? "it" : "all of them"}. `,
    el("button", { type: "button", onclick: () => { state.ticked.clear(); render(); } }, "Clear selection"),
  );
}

// The sample's progress in the sidebar, under the "Units" filter: one bar per role it selects.
let sampleProgressLoad = 0;
async function loadSampleProgress() {
  const load = ++sampleProgressLoad;
  try {
    const progress = await api("/api/sample-progress");
    if (load === sampleProgressLoad) renderSampleProgress(progress);
  } catch {
    // leave the bars as they are; they are refreshed with the next unit
  }
}

function renderSampleProgress(progress) {
  const box = $("sample-progress");
  const rows = sampleProgressRows(progress, state.filters.sample || $("queue-sample").value);
  box.hidden = rows.length === 0;
  box.replaceChildren(...rows.map((row) => el("div", { class: `sample-progress-row ${row.role}` },
    el("div", { class: "sample-progress-head" },
      el("span", { class: "sample-progress-label" }, row.heading),
      el("span", { class: "sample-progress-percent" }, row.percentText)),
    el("div", { class: "sample-progress-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(row.percent), "aria-label": row.heading },
      el("div", { class: "sample-progress-fill", style: { width: `${Math.min(row.percent, 100)}%` } })),
    el("div", { class: "sample-progress-counts" }, row.units))));
}

// The rail's "Suggestions" section: "Save all suggestions" and "Save all reasons" for the unit on screen. Always there
// (greyed out when there is nothing to save), so the buttons can be found; updated in place, never rebuilt mid-click.
function updateRailSuggestions() {
  const section = $("pair-nav").querySelector(".rail-suggestions");
  if (!section || !state.unit) return;
  // not on test units: their suggestions are never shown
  section.hidden = state.config.read_only || state.unit.sample_role === "test";
  if (section.hidden) return;
  const pairs = visible();
  const hasSuggestions = pairs.some((pair) => pair.suggestions.length);
  const counts = suggestionCounts(pairs);
  section.querySelector(".rail-note").textContent = hasSuggestions ? "" : "No suggestions in this unit yet";
  setButton(section.querySelector(".save-labels"), {
    text: `Save all (${counts.labels})`, disabled: counts.labels === 0,
    title: "Label every unlabelled pair whose suggestions agree, with the suggested category, reason and certainty. Labelled pairs are left as they are.",
  });
  setButton(section.querySelector(".save-reasons"), {
    text: `Save reasons (${counts.reasons})`, disabled: counts.reasons === 0,
    title: "Give each pair you labelled without a reason the reason of a suggestion for the same category. Your own reasons are kept.",
  });
}

async function saveSuggestions(kind) {
  const unit = state.unit;
  if (!unit) return;
  const counts = suggestionCounts(visible());
  const count = kind === "labels" ? counts.labels : counts.reasons;
  if (!count) return;
  const question = kind === "labels"
    ? `Save the suggested category for ${countOf(count, "pair")} in ${unit.id}?`
    : `Save the suggested reason for ${countOf(count, "pair")} in ${unit.id}?`;
  if (!window.confirm(question)) return;
  try {
    const pairs = visible().filter((pair) => !pair.rule).map((pair) => [pair.first, pair.second]);
    const result = await api("/api/save-suggestions", { unit_id: unit.id, kind, pairs });
    if (state.unit?.id === unit.id) {
      applyWrite(result);
      render();
    }
    await reloadUnit();
    const what = kind === "labels" ? "suggested label" : "suggested reason";
    showStatus(`Saved ${countOf(result.written.length, what)}`);
  } catch (error) {
    showStatus(error.message, true);
  }
}

function formatWhen(when) {
  return when ? when.replace("T", " ").replace("Z", " UTC") : "";
}

function provenanceText(pair) {
  const provenance = pair.provenance;
  if (!provenance) return "Not classified.";
  const parts = [pair.source === "rule" ? `Labelled by ${provenance.resp.replace("#rule-", "rule: ")}` : `Saved by ${provenance.resp || "unknown"}`];
  if (provenance.when) parts.push(formatWhen(provenance.when));
  if (provenance.cert) parts.push(`certainty ${provenance.cert}`);
  if (provenance.model) parts.push(`model ${provenance.model}`);
  if (provenance.definitions_hash) parts.push(`definitions ${provenance.definitions_hash.slice(0, 8)}`);
  if (provenance.justification_source) parts.push(`reason copied from ${provenance.justification_source}`);
  return parts.join(" · ");
}

// Long category names wrap after an underscore rather than mid-word.
function breakable(name) {
  return name.replaceAll("_", "_\u200b");
}

function pipelineMark() {
  return el("img", { src: "static/favicon.svg", alt: "rdgai", class: "rdgai-mark", title: "Labelled by the rdgai pipeline (tentative)" });
}

function certaintyOf(pair) {
  return pair.provenance?.cert || state.pendingCert[pairKey(pair)] || "high";
}

function reasonOf(pair) {
  const key = pairKey(pair);
  return key in state.reasons ? state.reasons[key] : pair.reason;
}

function compactMode() {
  return state.unit ? isCompact(state.unit, state.config.compact_threshold) : false;
}

// In compact mode a pair is a one-line row unless expanded; otherwise always a full panel.
function pairView(pair, index) {
  return compactMode() && !state.expanded.has(pairKey(pair)) ? compactRow(pair, index) : pairPanel(pair, index);
}

// The heading of a group of compact rows: the first reading they all start from.
function groupHeading(unit, id) {
  const reading = unit.readings.find((candidate) => candidate.id === id);
  return el("h3", { class: "group-head" },
    el("span", { class: "group-from" }, "From"),
    el("span", { class: "group-id" }, id),
    el("span", { class: "group-text" }, reading ? reading.text || "OMIT" : id),
    reading ? el("span", { class: "group-witnesses" }, `${reading.witness_count} witness${reading.witness_count === 1 ? "" : "es"}`) : null);
}

function pairViews(unit, pairs, offset) {
  if (!compactMode()) return pairs.map((pair, index) => pairView(pair, offset + index));
  let index = offset;
  return groupByFirst(pairs).map((group) => el("section", { class: "group" },
    groupHeading(unit, group.first),
    ...group.pairs.map((pair) => pairView(pair, index++)),
  ));
}

// The rail's "Select pairs like this one" section (compact units): select all pairs with the same suggestion or the
// same change as the current pair. Updated in place as the current pair changes.
function updateRailSelect() {
  const section = $("pair-nav").querySelector(".rail-select");
  if (!section || !state.unit) return;
  section.hidden = !compactMode() || state.config.read_only;
  if (section.hidden) return;
  const pairs = visible();
  const focused = pairs[state.focused];
  const bySuggestion = focused ? sameSuggestion(pairs, focused) : [];
  const byChange = focused ? sameChange(pairs, focused) : [];
  const same = section.querySelector(".select-same-suggestion");
  setButton(same, { text: `Same suggestion (${bySuggestion.length})`, disabled: !bySuggestion.length,
    title: focused?.suggestions[0] ? `Select the pairs also suggested ${focused.suggestions[0].category}` : "The current pair has no suggestion" });
  same.dataset.tickKeys = bySuggestion.join(" ");
  const change = section.querySelector(".select-same-change");
  setButton(change, { text: `Same change (${byChange.length})`, disabled: !byChange.length,
    title: focused?.change?.text ? `Select the pairs where also: ${focused.change.text}` : "" });
  change.dataset.tickKeys = byChange.join(" ");
}

function selectKeys(keys) {
  state.ticked = new Set((keys || "").split(" ").filter(Boolean));
  render();
}

function renderPairs(unit) {
  $("main").classList.toggle("wide", compactMode());
  const rulePairs = unit.rule_pairs || [];
  const children = [];
  if (state.showTranslation && unit.pairs.length && !Object.keys(unit.translations || {}).length) {
    const request = "Translate the verse I'm on.";
    children.unshift(el("div", { class: "translation-hint" },
      el("span", { class: "muted" }, "No translation yet. Ask Claude in Desktop: "),
      el("q", {}, request),
      el("button", { type: "button", class: "copy-request", title: "Copy this request to paste into Claude Desktop",
        onclick: () => navigator.clipboard?.writeText(request).then(() => showStatus("Copied: paste it into Claude Desktop"), () => {}) }, "Copy"),
      el("span", { class: "muted" }, " The translation appears here by itself once Claude has saved it.")));
  }
  children.push(...pairViews(unit, unit.pairs, 0));
  if (rulePairs.length) {
    const details = el("details", { class: "rule-section", open: state.showRules },
      el("summary", {}, `${countOf(rulePairs.length, "pair")} labelled by rule`, el("span", { class: "muted" }, " (subreadings; expand to check or override)")),
      ...pairViews(unit, rulePairs, unit.pairs.length),
    );
    details.addEventListener("toggle", () => {
      if (state.showRules === details.open) return;
      state.showRules = details.open;
      if (!state.showRules && state.focused >= unit.pairs.length) state.focused = Math.max(unit.pairs.length - 1, 0);
    });
    children.push(details);
  }
  $("pairs").replaceChildren(...children);
}

// The disclosure triangle that expands a compact row into the full panel (and back): always the row's first element.
function disclosure(key, expanded) {
  return el("button", {
    type: "button", class: "disclosure", "aria-expanded": String(expanded), "data-expand": key,
    title: expanded ? "Collapse to a row (Enter)" : "Expand: cards, verse, certainty and reason (Enter)",
    "aria-label": expanded ? "Collapse" : "Expand",
  }, el("span", { class: "triangle", "aria-hidden": "true" }));
}

function compactRow(pair, index) {
  const readOnly = state.config.read_only;
  const key = pairKey(pair);
  const suggestion = pair.suggestions[0];
  const suggested = new Set(pair.suggestions.map((item) => item.category));
  const byRule = pair.source === "rule" || (pair.rule && !pair.source);
  const label = pair.category || (pair.rule ? ruleText(pair.rule).replace("By rule: ", "rule: ") : "—");
  return el("article", { class: `pair compact${index === state.focused ? " focused" : ""}${byRule ? " rule-pair" : ""}${state.ticked.has(key) ? " ticked" : ""}`, "data-index": String(index) },
    disclosure(key, false),
    el("button", { type: "button", class: "ids select", "aria-pressed": String(state.ticked.has(key)), disabled: readOnly,
      title: "Select this pair, to label several at once (x)", "data-tick": key }, `${pair.first} → ${pair.second}`),
    el("div", { class: "row-main" },
      // the first reading is in the group heading ("From a: …"), so the row shows where it goes to
      el("div", { class: "texts", title: `${pair.first_text} → ${pair.second_text}` }, el("span", { class: "arrow" }, "➜"), pair.second_text),
      pair.change ? el("div", { class: "change" }, pair.change.text) : null,
      pair.warning ? el("div", { class: "warning" }, `Check: ${pair.warning}`) : null,
    ),
    el("div", { class: "row-label" },
      el("div", { class: pair.category ? "current-label" : "muted" }, pair.source === "pipeline" ? pipelineMark() : null, label),
      suggestion ? el("div", { class: "muted" }, "suggested ", el("strong", {}, suggestion.category)) : null,
    ),
    el("div", { class: "mini" }, state.config.buttons.map((button) => el("button", {
      type: "button",
      class: [pair.category === button.name ? "current" : "", suggested.has(button.name) ? "suggested" : ""].join(" ").trim(),
      title: categoryTitle(button),
      disabled: readOnly,
      "data-category": button.name,
    }, button.short || button.name))),
  );
}

function toggleExpanded(key) {
  if (state.expanded.has(key)) state.expanded.delete(key);
  else state.expanded.add(key);
  render();
}

function pairPanel(pair, index) {
  const readOnly = state.config.read_only;
  const key = pairKey(pair);
  const suggested = new Set(pair.suggestions.map((suggestion) => suggestion.category));
  const buttons = state.config.buttons.map((button) => el("button", {
    type: "button",
    class: [pair.category === button.name ? "current" : "", suggested.has(button.name) ? "suggested" : ""].join(" ").trim(),
    title: categoryTitle(button),
    style: { gridRow: String(button.row + 1), gridColumn: String(button.column + 1) },
    disabled: readOnly,
    "data-category": button.name,
  }, button.key ? el("span", { class: "key" }, button.key.replace("shift+", "⇧")) : null, breakable(button.name)));

  const suggestions = pair.suggestions.map((suggestion, suggestionIndex) => el("div", { class: "suggestion muted" },
    `Suggested by ${suggestion.source}: `, el("strong", {}, suggestion.category),
    suggestion.justification ? ` — ${suggestion.written_for ? `(written for ${suggestion.written_for[0]} → ${suggestion.written_for[1]}) ` : ""}${suggestion.justification} ` : "",
    canUseReason(suggestion, readOnly)
      ? el("button", { type: "button", class: "copy-reason", title: "Copy this reason into the reason box (recorded as copied unless you edit it)", "data-copy-reason": String(suggestionIndex) }, "Use reason")
      : null));

  const cert = certaintyOf(pair);
  const byRule = pair.source === "rule" || (pair.rule && !pair.source);
  // the readings (cards, verse, translation, witnesses) above; the decision in the box below, outlined when focused.
  // An expanded compact row is one box around all of it, with the disclosure triangle at its top left.
  const expanded = compactMode();
  return el("article", { class: `pair full${expanded ? " expanded" : ""}${index === state.focused ? " focused" : ""}${byRule ? " rule-pair" : ""}${state.ticked.has(key) ? " ticked" : ""}`, "data-index": String(index) },
    expanded ? el("div", { class: "expanded-head" },
      disclosure(key, true),
      el("button", { type: "button", class: "ids select", "aria-pressed": String(state.ticked.has(key)), disabled: readOnly,
        title: "Select this pair, to label several at once (x)", "data-tick": key }, `${pair.first} → ${pair.second}`)) : null,
    pairReadings(pair),
    el("div", { class: "decision" },
    el("div", { class: "categories" }, buttons),
    pair.warning ? el("div", { class: "warning", role: "note" }, `Check: ${pair.warning}`) : null,
    suggestions,
    el("div", { class: "pair-foot" },
      el("div", { class: "certainty" }, state.config.certainty.map((value) => el("button", {
        type: "button", class: value === cert ? "selected" : "", disabled: readOnly, title: value[0], "data-cert": value,
      }, value))),
      el("textarea", { class: "reason", rows: "1", placeholder: "Reason (saved as the justification)", disabled: readOnly, "data-reason": key }, reasonOf(pair)),
      el("div", { class: "provenance" }, pair.source === "pipeline" ? pipelineMark() : null,
        pair.rule ? `${ruleText(pair.rule)}${pair.source === "human" ? " (overridden by a human)" : ""} · ${provenanceText(pair)}` : provenanceText(pair)),
    ),
    ),
  );
}

// ---------------------------------------------------------------------------
// definitions panel (editable unless the server is read-only or the definitions are locked)
// ---------------------------------------------------------------------------

function renderDefinitions() {
  const config = state.config;
  const editable = config.definitions_editable;
  const editing = state.editing || {};
  const names = config.buttons.map((button) => button.name);

  const guidance = editing.kind === "guidance"
    ? el("form", { class: "definition-form", "data-form": "guidance" },
        el("textarea", { name: "guidance", rows: "8" }, config.guidance || ""),
        formButtons("Save guidance"))
    : el("div", {},
        config.guidance ? el("pre", {}, config.guidance) : el("p", { class: "muted" }, "No guidance."),
        editable ? el("button", { type: "button", "data-edit": "guidance" }, "Edit guidance") : null);

  const categories = config.buttons.map((button) => {
    if (editing.kind === "category" && editing.name === button.name) {
      return el("form", { class: "definition-form", "data-form": "category", "data-name": button.name },
        el("label", {}, "Name ", el("input", { name: "new_name", value: button.name, size: "28" })),
        el("textarea", { name: "description", rows: "6" }, button.description),
        el("p", { class: "muted" }, "Renaming also updates every label and suggestion that uses the category."),
        formButtons("Save"));
    }
    if (editing.kind === "remove" && editing.name === button.name) {
      return el("form", { class: "definition-form", "data-form": "remove", "data-name": button.name },
        el("p", {}, `Remove ${button.name}${button.inverse !== button.name ? ` and its inverse ${button.inverse}` : ""}?`),
        el("label", {}, "Move its labels to ", el("select", { name: "reassign_to" },
          el("option", { value: "" }, "— nothing (only if unused) —"),
          ...names.filter((name) => name !== button.name && name !== button.inverse).map((name) => el("option", { value: name }, name)))),
        formButtons("Remove"));
    }
    return el("section", {},
      el("h3", {}, button.name, button.inverse !== button.name ? el("span", { class: "muted" }, ` ↔ ${button.inverse}`) : null),
      el("p", {}, button.description),
      el("div", { class: "definition-actions" },
        el("a", { href: routeHash({ view: "category", category: button.name, params: {} }) }, "Pairs"),
        editable ? el("button", { type: "button", "data-edit": "category", "data-name": button.name }, "Edit") : null,
        editable ? el("button", { type: "button", "data-edit": "remove", "data-name": button.name }, "Remove") : null));
  });

  const add = !editable ? null : editing.kind === "add"
    ? el("form", { class: "definition-form", "data-form": "add" },
        el("h3", {}, "New category"),
        el("label", {}, "Name ", el("input", { name: "name", size: "28", required: true })),
        el("textarea", { name: "description", rows: "4", placeholder: "Description", required: true }),
        el("label", {}, "Inverse (optional) ", el("input", { name: "inverse_name", size: "28" })),
        el("textarea", { name: "inverse_description", rows: "3", placeholder: "Description of the inverse (if it has one)" }),
        formButtons("Add"))
    : el("button", { type: "button", "data-edit": "add" }, "Add a category");

  $("definitions").replaceChildren(
    el("h2", {}, "Definitions"),
    el("p", { class: "muted" }, `Version ${config.definitions_hash.slice(0, 8)} · ${config.direction_convention}`),
    ...[!editable ? el("p", { class: "muted" }, config.read_only ? "Read-only: the definitions cannot be edited." : "The definitions are locked on this server.") : null,
      guidance, ...categories, add].filter(Boolean),
  );
}

function formButtons(label) {
  return el("div", { class: "definition-actions" },
    el("button", { type: "submit", class: "primary" }, label),
    el("button", { type: "button", "data-edit": "" }, "Cancel"));
}

function onDefinitionsClick(event) {
  const button = event.target.closest("[data-edit]");
  if (!button) return;
  state.editing = button.dataset.edit ? { kind: button.dataset.edit, name: button.dataset.name } : null;
  renderDefinitions();
  $("definitions").querySelector("form textarea, form input")?.focus();
}

async function onDefinitionsSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const values = Object.fromEntries(new FormData(form));
  const name = form.dataset.name;
  const requests = {
    guidance: () => api("/api/definitions/guidance", { guidance: values.guidance }),
    category: () => api("/api/definitions/category", {
      name, description: values.description, new_name: values.new_name.trim() !== name ? values.new_name.trim() : null,
    }),
    remove: () => api("/api/definitions/remove", { name, reassign_to: values.reassign_to || null }),
    add: () => api("/api/definitions/add", {
      name: values.name.trim(), description: values.description,
      inverse_name: values.inverse_name.trim() || null, inverse_description: values.inverse_description,
    }),
  };
  try {
    const change = await requests[form.dataset.form]();
    state.editing = null;
    await refreshConfig();
    showStatus(change.message || "Definitions saved");
  } catch (error) {
    showStatus(error.message, true);
  }
}

async function refreshConfig() {
  state.config = await api("/api/config");
  fillCategoryFilter();
  renderDefinitions();
  await reloadView();
}

// ---------------------------------------------------------------------------
// by category: every pair with a category, with its context; re-label in place
// ---------------------------------------------------------------------------

const CATEGORY_PAGE = 100;

async function loadCategory(name, params = {}) {
  try {
    const query = filterQuery({ source: params.source || "", offset: params.offset || "", limit: CATEGORY_PAGE });
    const [counts, data] = await Promise.all([api("/api/categories"), api(`/api/category/${encodeURIComponent(name)}${query}`)]);
    state.category = { params, data, counts };
    state.version = data.version;
    renderCategory();
  } catch (error) {
    $("view-category").replaceChildren(el("p", { class: "muted" }, error.message));
  }
}

function categoryHash(changes) {
  const { data, params } = state.category;
  return routeHash({ view: "category", category: changes.category ?? data.category, params: {
    source: changes.source ?? (params.source || ""), offset: changes.offset ?? 0,
  } });
}

function pager(data) {
  const page = pageInfo(data.total, data.offset, data.limit);
  return el("div", { class: "pager" },
    el("span", { class: "muted" }, data.total ? `${page.from}–${page.to} of ${countOf(data.total, "pair")}` : "No pairs"),
    page.prev !== null ? el("a", { href: categoryHash({ offset: page.prev }) }, "← Previous") : null,
    page.next !== null ? el("a", { href: categoryHash({ offset: page.next }) }, "Next →") : null);
}

function renderCategory() {
  const { data, counts, params } = state.category;
  const button = state.config.buttons.find((candidate) => candidate.name === data.category);
  const count = counts.categories.find((row) => row.category === data.category);
  const categorySelect = el("select", { title: "Category", onchange: (event) => { location.hash = categoryHash({ category: event.target.value, offset: 0 }); } },
    counts.categories.map((row) => el("option", { value: row.category, selected: row.category === data.category }, `${row.category} (${row.total})`)));
  const sourceSelect = el("select", { title: "Who labelled the pairs", onchange: (event) => { location.hash = categoryHash({ source: event.target.value, offset: 0 }); } },
    [["", "Any source"], ["human", "Human"], ["pipeline", "Pipeline"], ["rule", "Rule"]].map(([value, label]) =>
      el("option", { value, selected: (params.source || "") === value }, label)));
  $("view-category").replaceChildren(
    el("div", { class: "view-head" },
      el("h1", {}, data.category),
      categorySelect, sourceSelect,
      count ? el("span", { class: "muted" }, `${count.human} human · ${count.pipeline} pipeline · ${count.rule} by rule`) : null),
    ...[button ? el("p", { class: "category-description" }, button.description,
      button.inverse !== button.name ? el("span", { class: "muted" }, ` Inverse: ${button.inverse}.`) : null) : null].filter(Boolean),
    pager(data),
    el("div", { class: "category-rows" }, data.pairs.map(categoryRow)),
    ...(data.pairs.length > 10 ? [pager(data)] : []),
  );
}

function categoryRow(pair, index) {
  const readOnly = state.config.read_only;
  const unitLink = unitHash(pair.unit_id, { sample: "all" });
  return el("article", { class: `category-row${pair.source === "rule" ? " rule-pair" : ""}`, "data-index": String(index) },
    el("div", { class: "row-top" },
      el("a", { href: unitLink, class: "unit-link", title: "Open the unit" }, pair.unit_id),
      pair.sample_role ? el("span", { class: "badge" }, pair.sample_role) : null,
      el("span", { class: "muted" }, `${pair.first} → ${pair.second}`),
      pair.source === "pipeline" ? pipelineMark() : null),
    el("p", { class: "verse" }, pair.context.before ? `${pair.context.before} ` : "",
      el("span", { class: "unit-text" }, pair.context.lemma || "⸆"), pair.context.after ? ` ${pair.context.after}` : ""),
    el("p", { class: "relation" }, pair.first_text, el("span", { class: "arrow" }, "➜"), pair.second_text),
    pair.change ? el("div", { class: "change" }, pair.change.text) : null,
    translationBlock(pair),
    pair.warning ? el("div", { class: "warning" }, `Check: ${pair.warning}`) : null,
    pair.reason ? el("p", { class: "reason-text" }, `“${pair.reason}”`) : null,
    el("div", { class: "provenance" }, pair.rule ? `${ruleText(pair.rule)} · ${provenanceText(pair)}` : provenanceText(pair)),
    el("div", { class: "mini" }, state.config.buttons.map((button) => el("button", {
      type: "button",
      class: pair.category === button.name ? "current" : "",
      title: `Re-label as ${button.name}`,
      disabled: readOnly || pair.category === button.name,
      "data-relabel": button.name,
    }, button.short || button.name))),
  );
}

async function onCategoryClick(event) {
  const button = event.target.closest("[data-relabel]");
  if (!button || !state.category) return;
  const pair = state.category.data.pairs[Number(button.closest(".category-row").dataset.index)];
  const category = button.dataset.relabel;
  if (!pair || pair.category === category) return;
  // a human's own reason is kept; the pipeline's reasoning is not carried over as the human's
  const own = pair.source === "human";
  const request = labelRequest(pair.unit_id, [pair], category, pair.provenance?.cert || "high",
    own ? pair.reason : "", own ? pair.provenance?.justification_source || null : null);
  request.expected = expectedFor([pair]);
  try {
    await api("/api/label", request);
    showStatus(`${pair.unit_id} ${pair.first} → ${pair.second}: ${category}`);
  } catch (error) {
    showStatus(error.status === 409 ? "This pair was changed elsewhere; reloaded." : error.message, true);
  }
  await loadCategory(state.category.data.category, state.category.params);
}

// ---------------------------------------------------------------------------
// agreement: human labels compared with suggestions
// ---------------------------------------------------------------------------

async function loadAgreement(params = {}) {
  try {
    const data = await api(`/api/agreement${filterQuery({ scope: params.scope || "", source: params.source || "" })}`);
    state.agreement = { params, data };
    state.version = null;  // remembered on the next check
    renderAgreement();
  } catch (error) {
    $("view-agreement").replaceChildren(el("p", { class: "muted" }, error.message));
  }
}

const percent = (value) => `${(value * 100).toFixed(1)}%`;

function agreementHash(changes) {
  const { data } = state.agreement;
  return routeHash({ view: "agreement", params: { scope: changes.scope ?? data.scope, source: changes.source ?? (data.source || "") } });
}

function renderAgreement() {
  const { data } = state.agreement;
  const scopes = state.config.has_sample
    ? [["example", "Example units"], ["sampled", "Example and test units"], ["test", "Test units"], ["all", "All units"]]
    : [["all", "All units"]];
  const head = el("div", { class: "view-head" },  // the view's name is in the header
    el("select", { title: "Which units to compare", onchange: (event) => { location.hash = agreementHash({ scope: event.target.value }); } },
      scopes.map(([value, label]) => el("option", { value, selected: value === data.scope }, label))),
    data.sources.length ? el("select", { title: "Whose suggestions", onchange: (event) => { location.hash = agreementHash({ source: event.target.value }); } },
      data.sources.map((row) => el("option", { value: row.source, selected: row.source === data.source }, `${row.source} (${row.count} suggestions)`))) : null);
  const children = [head];
  if (data.scope === "sampled" || data.scope === "test" || (data.scope === "all" && state.config.has_sample)) {
    children.push(el("p", { class: "warning" }, "Includes test units: these suggestions are for units held out for evaluation."));
  }
  if (!data.sources.length) {
    children.push(el("p", { class: "muted" }, "No suggestions in these units yet. Suggestions are made through MCP (suggest_classification), e.g. by Claude."));
    return $("view-agreement").replaceChildren(...children);
  }
  const scores = data.metrics;
  children.push(
    el("p", { class: "agreement-summary" },
      el("strong", {}, `${data.agreed} of ${data.compared}`), ` pairs labelled by a human agree with ${data.source}`,
      data.compared ? ` (${percent(scores.accuracy)}; macro F1 ${percent(scores.macro_f1)}).` : "."),
    el("p", { class: "muted" }, "Only pairs with both a human label and a suggestion are compared, in display direction."));
  if (data.compared) {
    children.push(
      el("h2", {}, "Per category"),
      el("table", { class: "scores" },
        el("thead", {}, el("tr", {}, ["Category", "Human labels", "Suggested", "Agree (recall)", "Precision", "F1"].map((heading) => el("th", {}, heading)))),
        el("tbody", {}, scores.per_category.map((row) => el("tr", { class: row.indicative ? "indicative" : "", title: row.indicative ? "Fewer than 10 pairs: indicative only" : null },
          el("th", {}, el("a", { href: routeHash({ view: "category", category: row.category, params: {} }) }, row.category)),
          el("td", {}, String(row.support)), el("td", {}, String(row.predicted)),
          el("td", {}, percent(row.recall)), el("td", {}, percent(row.precision)), el("td", {}, percent(row.f1)))))),
      el("h2", {}, "Confusion"),
      confusionTable(scores.confusion),
      el("h2", {}, `Disagreements (${data.disagreements.length})`),
      el("div", { class: "disagreements" }, data.disagreements.map((row) => el("a", {
        class: "disagreement", href: unitHash(row.unit_id, { sample: data.scope === "all" ? "all" : data.scope, disagreement: "1" }),
        title: "Open the unit (Next goes through the units with disagreements)",
      },
        el("span", { class: "unit-link" }, `${row.unit_id} ${row.first} → ${row.second}`),
        el("span", { class: "texts" }, row.first_text, el("span", { class: "arrow" }, "➜"), row.second_text),
        el("span", {}, "human ", el("strong", {}, row.human), " · suggested ", el("strong", {}, row.suggested)),
        row.justification ? el("span", { class: "muted" }, row.justification) : null))));
  }
  $("view-agreement").replaceChildren(...children);
}

function confusionTable(confusion) {
  return el("div", { class: "confusion-wrap" }, el("table", { class: "confusion" },
    el("thead", {}, el("tr", {}, el("th", { class: "corner" }, "human ↓ · suggested →"), confusion.labels.map((label) => el("th", { class: "rotate" }, el("span", {}, label))))),
    el("tbody", {}, confusion.matrix.map((row, rowIndex) => {
      const total = row.reduce((sum, value) => sum + value, 0);
      return el("tr", {}, el("th", {}, confusion.labels[rowIndex]), row.map((value, columnIndex) => {
        const share = total ? value / total : 0;
        return el("td", {
          class: columnIndex === rowIndex ? "diagonal" : "",
          style: { backgroundColor: `rgba(148, 0, 0, ${(share * 0.85).toFixed(2)})`, color: share > 0.5 ? "#fff" : "" },
        }, value ? String(value) : "");
      }));
    }))));
}

function syncFilterControls() {
  $("queue-sample").value = state.filters.sample || "all";
  $("queue-status").value = state.filters.status || "all";
  $("queue-verse").value = state.filters.verse || "";
  $("queue-category").value = state.filters.category || "";
  $("queue-cert").value = state.filters.cert || "";
  $("queue-source").value = state.filters.source || "";
  $("queue-disagreement").checked = Boolean(state.filters.disagreement);
}

// ---------------------------------------------------------------------------
// the header's right side (title or file, annotator) and the profile form
// ---------------------------------------------------------------------------

function renderHeaderRight() {
  const config = state.config;
  $("header-file").replaceChildren(...[
    el("div", { class: "file-text" },
      el("span", { class: "file-name", title: config.file }, config.title || config.file),
      config.title ? el("span", { class: "file-sub" }, config.file) : null),
    config.read_only ? el("span", { class: "mode" }, "read-only") : null,
    config.evaluation ? el("span", { class: "mode", title: "Evaluation mode: the test units' labels are hidden" }, "evaluation") : null,
  ].filter(Boolean));
  const user = headerUser(config.profile);
  $("header-user").replaceChildren(el("button", { type: "button", class: "user", title: "Your name and the header's details", onclick: () => togglePanel("profile") },
    ...(user
      ? [user.name ? el("span", { class: "user-name" }, user.name) : null, el("span", { class: "avatar" }, user.short)].filter(Boolean)
      : [el("span", { class: "user-name" }, "No annotator set")])));
}

function renderProfile() {
  const config = state.config;
  const profile = config.profile;
  const editable = !config.read_only;
  const field = (label, name, value, help, attrs = {}) => el("label", { class: "field" }, label,
    el("input", { name, value: value || "", disabled: !editable, ...attrs }), help ? el("span", { class: "side-help" }, help) : null);
  $("profile").replaceChildren(el("form", { class: "profile-form", onsubmit: saveProfile },
    el("h2", {}, "Profile and header"),
    profile.id
      ? el("p", { class: "muted" }, "Labels are saved as ", el("code", {}, `#${profile.id}`), " (set when the server starts, with --annotator).")
      : el("p", { class: "muted" }, "No annotator is set: start the server with --annotator (e.g. --annotator RT) to record who saves labels."),
    profile.id ? field("Full name", "name", profile.name, "Recorded in the TEI header, as the name for this annotator.") : null,
    profile.id ? field("Short name", "short_name", profile.short_name, "Shown in the circle at the top right. Leave empty for your initials.", { maxlength: "6" }) : null,
    field("Header title", "title", config.title, `Shown at the top right instead of the file name (${config.file}). Leave empty for the file name.`),
    el("div", { class: "definition-actions" },
      el("button", { type: "submit", class: "primary", disabled: !editable }, "Save"),
      el("button", { type: "button", onclick: () => togglePanel("profile") }, "Close")),
    !editable ? el("p", { class: "muted" }, "This server is read-only.") : null,
  ));
}

async function saveProfile(event) {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  try {
    await api("/api/profile", values);
    state.config = await api("/api/config");
    renderHeaderRight();
    renderProfile();
    showStatus("Saved");
  } catch (error) {
    showStatus(error.message, true);
  }
}

function showQueueCount(total) {
  $("queue-count").textContent = total === null ? "This unit is not in these units." : `${total} unit${total === 1 ? "" : "s"} match.`;
}

function fillCategoryFilter() {
  const select = $("queue-category");
  select.replaceChildren(el("option", { value: "" }, "Any category"),
    ...state.config.buttons.map((button) => el("option", { value: button.name }, button.name)));
  select.value = state.filters.category || "";
}

// ---------------------------------------------------------------------------
// actions
// ---------------------------------------------------------------------------

let statusTimer = null;
function showStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.className = `status show${isError ? " error" : ""}`;
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { status.className = "status"; }, isError ? 5000 : 1500);
}

function applyWrite(result) {
  const updated = Object.fromEntries(result.pairs.map((pair) => [pairKey(pair), pair]));
  state.unit.pairs = state.unit.pairs.map((pair) => updated[pairKey(pair)] || pair);
  state.unit.rule_pairs = (state.unit.rule_pairs || []).map((pair) => updated[pairKey(pair)] || pair);
  state.unit.progress = result.progress;
  state.unit.status = result.status;
  for (const key of Object.keys(updated)) {
    delete state.reasons[key];
    delete state.pendingCert[key];
    delete state.copied[key];
  }
}

async function write(path, body, message) {
  try {
    const result = await api(path, body);
    applyWrite(result);
    state.ticked.clear();
    render();
    // fetch the unit again: other labels may have changed too (subreading rules, or another process)
    await reloadUnit();
    showStatus(message);
    return true;
  } catch (error) {
    if (error.status === 409) {
      showStatus("This pair was changed elsewhere (e.g. through Claude); reloaded.", true);
      await reloadUnit();
    } else {
      showStatus(error.message, true);
    }
    return false;
  }
}

function targets() {
  return targetPairs(visible(), state.focused, state.ticked);
}

// scroll: whether moving on to the next pair afterwards scrolls it into view (from the keyboard; never after a click)
async function chooseCategory(name, { scroll = true } = {}) {
  const pairs = targets();
  if (!pairs.length || state.config.read_only) return;
  const single = pairs.length === 1;
  const category = single ? categoryClick(pairs[0].category, name).category : (pairs.every((pair) => pair.category === name) ? null : name);
  const focusedPair = visible()[state.focused];
  const reasonSource = single ? pairs[0] : (pairs.includes(focusedPair) ? focusedPair : null);
  const justification = reasonSource ? reasonOf(reasonSource) : "";
  const source = reasonSource ? justificationSource(justification, state.copied[pairKey(reasonSource)]) : null;
  const cert = certaintyOf(single ? pairs[0] : focusedPair);
  const saved = await write("/api/label", labelRequest(state.unit.id, pairs, category, cert, justification, source),
    category ? `Saved ${category}${single ? "" : ` for ${pairs.length} pairs`}` : "Cleared");
  if (saved && single && category && state.focused < visible().length - 1) {
    moveFocus(1, { scroll });
  }
}

async function chooseCertainty(value) {
  const pairs = targets();
  if (!pairs.length || state.config.read_only) return;
  for (const pair of pairs.filter((pair) => !pair.category)) state.pendingCert[pairKey(pair)] = value;
  const labelled = pairs.filter((pair) => pair.category);
  if (labelled.length) {
    await write("/api/certainty", certaintyRequest(state.unit.id, labelled, value), `Certainty ${value}`);
  } else {
    render();
  }
}

async function saveReason(key) {
  const pair = visible().find((candidate) => pairKey(candidate) === key);
  if (!pair || !pair.category || !(key in state.reasons) || state.reasons[key] === pair.reason) return;
  const source = justificationSource(state.reasons[key], state.copied[key]);
  await write("/api/label", labelRequest(state.unit.id, [pair], pair.category, certaintyOf(pair), state.reasons[key], source), "Reason saved");
}

// "Use reason": a labelled pair saves the reason straight away; an unlabelled one keeps it for the category chosen next
async function copyReason(pairIndex, suggestionIndex) {
  const pair = visible()[pairIndex];
  const suggestion = pair?.suggestions[suggestionIndex];
  if (!suggestion) return;
  const key = pairKey(pair);
  const text = usableReason(suggestion);
  state.reasons[key] = text;
  state.copied[key] = { text, source: suggestion.source.split(":")[0] };
  render();  // focus stays on the pair, so a number key can save the label with this reason straight away
  if (pair.category) await saveReason(key);
  else showStatus("Reason ready: choose a category to save it with");
}

function moveFocus(delta, { scroll = true } = {}) {
  const count = visible().length;
  if (!count) return;
  state.focused = Math.max(0, Math.min(count - 1, state.focused + delta));
  for (const panel of document.querySelectorAll(".pair")) {
    panel.classList.toggle("focused", Number(panel.dataset.index) === state.focused);
  }
  if (scroll) document.querySelector(`.pair[data-index="${state.focused}"]`)?.scrollIntoView({ block: "nearest" });
  updatePairNav();
  updateRailSelect();
}

function toggleTick(key) {
  if (state.ticked.has(key)) state.ticked.delete(key);
  else state.ticked.add(key);
  render();
}

function togglePanel(id) {
  const panel = $(id);
  const show = panel.hidden;
  for (const other of ["help", "definitions", "profile"]) $(other).hidden = true;
  panel.hidden = !show;
  if (id === "definitions" && show) renderDefinitions();
  if (id === "profile" && show) renderProfile();
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------

function setUpEvents() {
  $("pairs").addEventListener("click", (event) => {
    const tickButton = event.target.closest("[data-tick-keys]");
    if (tickButton) {
      state.ticked = new Set(tickButton.dataset.tickKeys.split(" ").filter(Boolean));
      return render();
    }
    const panel = event.target.closest(".pair");
    if (!panel) return;
    const index = Number(panel.dataset.index);
    // a click focuses the pair where it is: the page doesn't move under the pointer
    if (index !== state.focused) moveFocus(index - state.focused, { scroll: false });
    const selectButton = event.target.closest("[data-tick]");
    if (selectButton) return toggleTick(selectButton.dataset.tick);
    const categoryButton = event.target.closest("[data-category]");
    if (categoryButton) return chooseCategory(categoryButton.dataset.category, { scroll: false });
    const certButton = event.target.closest("[data-cert]");
    if (certButton) return chooseCertainty(certButton.dataset.cert);
    const copyButton = event.target.closest("[data-copy-reason]");
    if (copyButton) return copyReason(index, Number(copyButton.dataset.copyReason));
    const expandButton = event.target.closest("[data-expand]");
    if (expandButton) return toggleExpanded(expandButton.dataset.expand);
  });
  // the rail: going to a pair is navigation, so it scrolls the pair to the top (below the header)
  $("pair-nav").addEventListener("click", (event) => {
    const item = event.target.closest("[data-nav-index]");
    if (!item) return;
    const index = Number(item.dataset.navIndex);
    if (index !== state.focused) moveFocus(index - state.focused, { scroll: false });
    document.querySelector(`.pair[data-index="${index}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  });
  $("pairs").addEventListener("input", (event) => {
    if (event.target.dataset.reason) state.reasons[event.target.dataset.reason] = event.target.value;
  });
  $("pairs").addEventListener("focusout", (event) => {
    if (event.target.dataset.reason) saveReason(event.target.dataset.reason);
  });

  document.addEventListener("keydown", (event) => {
    if (!state.config) return;
    const action = keyAction({
      key: event.key, code: event.code, shiftKey: event.shiftKey, ctrlKey: event.ctrlKey,
      metaKey: event.metaKey, altKey: event.altKey, inTextField: event.target.matches("textarea, input, select"), view: state.view,
    }, state.config.buttons);
    if (!action) return;
    event.preventDefault();
    switch (action.type) {
      case "category": return chooseCategory(action.name);
      case "certainty": return chooseCertainty(action.value);
      case "move": return moveFocus(action.delta);
      case "toggle_tick": {
        const pair = visible()[state.focused];
        return pair && toggleTick(pairKey(pair));
      }
      case "next_unclassified": return goToUnit(state.unit?.next_unclassified);
      case "expand": {
        const pair = visible()[state.focused];
        return pair && compactMode() && toggleExpanded(pairKey(pair));
      }
      case "help": return togglePanel("help");
      case "translation": return toggleTranslation();
      case "leave_text": return document.activeElement?.blur();
    }
  });

  const onFilterChange = () => {
    state.filters = {
      sample: $("queue-sample").value, status: $("queue-status").value, verse: $("queue-verse").value.trim(),
      category: $("queue-category").value, cert: $("queue-cert").value, source: $("queue-source").value,
      disagreement: $("queue-disagreement").checked ? "1" : "",
    };
    state.lastUnitId = null;
    location.hash = routeHash({ view: "queue", filters: state.filters });
  };
  for (const id of ["queue-sample", "queue-status", "queue-verse", "queue-category", "queue-cert", "queue-source", "queue-disagreement"]) {
    $(id).addEventListener("change", onFilterChange);
  }
  for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => openView(button.dataset.view));
  $("view-category").addEventListener("click", onCategoryClick);
  $("definitions").addEventListener("click", onDefinitionsClick);
  $("definitions").addEventListener("submit", onDefinitionsSubmit);
  $("help-toggle").addEventListener("click", () => togglePanel("help"));
  $("translation-toggle").addEventListener("change", toggleTranslation);
  $("look").addEventListener("change", (event) => setLook(event.target.value));
  $("queue-reset").addEventListener("click", () => {
    state.filters = {};
    state.lastUnitId = null;
    location.hash = routeHash({ view: "queue", filters: {} });
  });
  $("definitions-toggle").addEventListener("click", () => togglePanel("definitions"));
  window.addEventListener("hashchange", route);
  window.addEventListener("focus", reloadCurrentUnit);
  document.addEventListener("visibilitychange", reloadCurrentUnit);
  window.addEventListener("pageshow", (event) => { if (event.persisted) reloadCurrentUnit(); });  // restored from the back/forward cache
  setInterval(checkForChanges, POLL_MILLISECONDS);
}

// Keeps the page in step with the file, which other processes (e.g. Claude through MCP) may change at any time.
async function checkForChanges() {
  if (state.view === "unit" && !state.unit) return;
  let latest;
  try {
    latest = (await api("/api/version")).version;
  } catch {
    return;  // the server may be restarting; try again on the next check
  }
  const typing = document.activeElement?.matches("textarea, input[type=search]") ?? false;
  const action = refreshAction({ hidden: document.hidden, typing, known: state.version, latest });
  if (action === "remember") state.version = latest;
  if (action === "reload") {
    await reloadView();
    showStatus("Updated: the file was changed elsewhere.");
  }
}

async function reloadView() {
  if (state.view === "category" && state.category) return loadCategory(state.category.data.category, state.category.params);
  if (state.view === "agreement" && state.agreement) return loadAgreement(state.agreement.params);
  if (state.view === "unit" && state.unit) return reloadUnit();
}

function reloadCurrentUnit() {
  const typing = document.activeElement?.matches("textarea, input, select") ?? false;
  if (!document.hidden && !typing) reloadView();
}

async function start() {
  state.config = await api("/api/config");
  renderHeaderRight();
  document.title = `rdgai annotate · ${state.config.file}`;
  if (!state.config.has_sample) {
    for (const value of ["sampled", "example", "test"]) $("queue-sample").querySelector(`option[value="${value}"]`).hidden = true;
  }
  document.body.classList.toggle("look-cards", state.look === "cards");
  syncTranslationToggle();
  $("look").value = state.look;
  fillCategoryFilter();
  setUpEvents();
  await route();
}

start().catch((error) => showStatus(error.message, true));

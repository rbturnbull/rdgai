// The rdgai annotation GUI. Talks to the JSON API in rdgai/gui/__init__.py; pure logic lives in logic.js.
import { keyAction, categoryClick, targetPairs, pairKey, labelRequest, certaintyRequest } from "./logic.js";

const state = {
  config: null,
  unit: null,
  focused: 0,
  ticked: new Set(),
  pendingCert: {},   // pairKey -> certainty chosen before a category (unlabelled pairs)
  reasons: {},       // pairKey -> edited reason text not yet saved
  filters: {},
};

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
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
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

function parseHash() {
  const match = /^#\/unit\/([^?]*)(\?.*)?$/.exec(location.hash);
  const params = new URLSearchParams((match && match[2]) || (location.hash.split("?")[1] ?? ""));
  return {
    unitId: match ? decodeURIComponent(match[1]) : null,
    filters: { sample: params.get("sample") || "", status: params.get("status") || "", verse: params.get("verse") || "" },
  };
}

function unitHash(unitId, filters = state.filters) {
  return `#/unit/${encodeURIComponent(unitId)}${filterQuery(filters)}`;
}

function goToUnit(unitId) {
  if (!unitId) return;
  const target = unitHash(unitId);
  if (location.hash === target) loadUnit(unitId);
  else location.hash = target;
}

async function route() {
  const { unitId, filters } = parseHash();
  state.filters = filters;
  if (unitId) {
    await loadUnit(unitId);
  } else {
    await goToQueueStart();
  }
}

async function goToQueueStart() {
  const queue = await api(`/api/queue${filterQuery()}`);
  state.filters = queue.filters;
  syncFilterControls();
  if (!queue.units.length) {
    state.unit = null;
    $("unit-header").replaceChildren(el("p", { class: "muted" }, "No units match these filters."));
    for (const id of ["context", "readings", "pairs"]) $(id).replaceChildren();
    $("bulk-bar").hidden = true;
    return;
  }
  const first = queue.units.find((unit) => unit.status === "unclassified" || unit.status === "partial") || queue.units[0];
  location.replace(unitHash(first.id));
  await loadUnit(first.id);
}

async function loadUnit(unitId, { keepFocus = false } = {}) {
  try {
    const unit = await api(`/api/unit/${encodeURIComponent(unitId)}${filterQuery()}`);
    const previousId = state.unit?.id;
    state.unit = unit;
    state.filters = unit.filters;
    if (!keepFocus || previousId !== unit.id) {
      state.focused = 0;
      state.ticked.clear();
      state.pendingCert = {};
      state.reasons = {};
    }
    state.focused = Math.min(state.focused, Math.max(unit.pairs.length - 1, 0));
    syncFilterControls();
    render();
    window.scrollTo({ top: 0 });
  } catch (error) {
    showStatus(error.message, true);
  }
}

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

function render() {
  const unit = state.unit;
  if (!unit) return;
  renderHeader(unit);
  renderContext(unit);
  renderReadings(unit);
  renderBulkBar();
  renderPairs(unit);
}

function renderHeader(unit) {
  const position = unit.position ? `${unit.position.index + 1} of ${unit.position.total}` : "not in this queue";
  $("unit-header").replaceChildren(
    el("div", { class: "unit-title" },
      el("h1", {}, unit.id),
      unit.sample_role ? el("span", { class: "badge" }, unit.sample_role) : null,
      el("span", { class: "muted" }, position),
      el("span", { class: "muted" }, `${unit.progress.pairs_done} / ${unit.progress.pairs_total} pairs`),
    ),
    el("div", { class: "nav" },
      el("button", { type: "button", disabled: !unit.prev, onclick: () => goToUnit(unit.prev) }, "← Prev"),
      el("button", { type: "button", disabled: !unit.next, onclick: () => goToUnit(unit.next) }, "Next →"),
      el("button", { type: "button", disabled: !unit.next_unclassified, title: "n", onclick: () => goToUnit(unit.next_unclassified) }, "Next unclassified"),
    ),
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

function renderReadings(unit) {
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
    `${state.ticked.size} pairs ticked: categories and certainty apply to all of them. `,
    el("button", { type: "button", onclick: () => { state.ticked.clear(); render(); } }, "Untick all"),
  );
}

function formatWhen(when) {
  return when ? when.replace("T", " ").replace("Z", " UTC") : "";
}

function provenanceText(pair) {
  const provenance = pair.provenance;
  if (!provenance) return "Not classified.";
  const parts = [`Saved by ${provenance.resp || "unknown"}`];
  if (provenance.when) parts.push(formatWhen(provenance.when));
  if (provenance.cert) parts.push(`certainty ${provenance.cert}`);
  if (provenance.model) parts.push(`model ${provenance.model}`);
  if (provenance.definitions_hash) parts.push(`definitions ${provenance.definitions_hash.slice(0, 8)}`);
  return parts.join(" · ");
}

function certaintyOf(pair) {
  return pair.provenance?.cert || state.pendingCert[pairKey(pair)] || "high";
}

function reasonOf(pair) {
  const key = pairKey(pair);
  return key in state.reasons ? state.reasons[key] : pair.reason;
}

function renderPairs(unit) {
  const readOnly = state.config.read_only;
  const panels = unit.pairs.map((pair, index) => {
    const key = pairKey(pair);
    const suggested = new Set(pair.suggestions.map((suggestion) => suggestion.category));
    const buttons = state.config.buttons.map((button) => el("button", {
      type: "button",
      class: [pair.category === button.name ? "current" : "", suggested.has(button.name) ? "suggested" : ""].join(" ").trim(),
      title: `${button.name}${button.inverse !== button.name ? ` (inverse: ${button.inverse})` : ""}\n\n${button.description}`,
      style: { gridRow: String(button.row + 1), gridColumn: String(button.column + 1) },
      disabled: readOnly,
      "data-category": button.name,
    }, button.key ? el("span", { class: "key" }, button.key.replace("shift+", "⇧")) : null, button.name));

    const suggestions = pair.suggestions.map((suggestion) => el("div", { class: "suggestion muted" },
      `Suggested by ${suggestion.source}: `, el("strong", {}, suggestion.category),
      suggestion.justification ? ` — ${suggestion.justification}` : ""));

    const cert = certaintyOf(pair);
    return el("article", { class: `pair${index === state.focused ? " focused" : ""}`, "data-index": String(index) },
      el("div", { class: "pair-head" },
        el("input", { type: "checkbox", title: "Tick (x)", checked: state.ticked.has(key), disabled: readOnly, "data-tick": key }),
        el("span", { class: "ids" }, `${pair.first} → ${pair.second}`),
        el("span", { class: "texts" }, pair.first_text, el("span", { class: "arrow" }, "→"), pair.second_text),
      ),
      el("div", { class: "categories" }, buttons),
      suggestions,
      el("div", { class: "pair-foot" },
        el("div", { class: "certainty" }, state.config.certainty.map((value) => el("button", {
          type: "button", class: value === cert ? "selected" : "", disabled: readOnly, title: value[0], "data-cert": value,
        }, value))),
        el("textarea", { class: "reason", rows: "1", placeholder: "Reason (saved as the justification)", disabled: readOnly, "data-reason": key }, reasonOf(pair)),
        el("div", { class: "provenance" }, provenanceText(pair)),
      ),
    );
  });
  $("pairs").replaceChildren(...panels);
}

function renderDefinitions() {
  const config = state.config;
  $("definitions").replaceChildren(
    el("h2", {}, "Definitions"),
    el("p", { class: "muted" }, `Version ${config.definitions_hash.slice(0, 8)} · ${config.direction_convention}`),
    config.guidance ? el("pre", {}, config.guidance) : null,
    ...config.buttons.map((button) => el("section", {},
      el("h3", {}, button.name, button.inverse !== button.name ? el("span", { class: "muted" }, ` ↔ ${button.inverse}`) : null),
      el("p", {}, button.description))),
  );
}

function syncFilterControls() {
  $("queue-sample").value = state.filters.sample || "all";
  $("queue-status").value = state.filters.status || "all";
  $("queue-verse").value = state.filters.verse || "";
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
  state.unit.progress = result.progress;
  state.unit.status = result.status;
  for (const key of Object.keys(updated)) {
    delete state.reasons[key];
    delete state.pendingCert[key];
  }
}

async function write(path, body, message) {
  try {
    const result = await api(path, body);
    applyWrite(result);
    state.ticked.clear();
    render();
    showStatus(message);
    return true;
  } catch (error) {
    if (error.status === 409) {
      showStatus("This pair was changed elsewhere (e.g. through Claude); reloaded.", true);
      await loadUnit(state.unit.id, { keepFocus: true });
    } else {
      showStatus(error.message, true);
    }
    return false;
  }
}

function targets() {
  return targetPairs(state.unit.pairs, state.focused, state.ticked);
}

async function chooseCategory(name) {
  const pairs = targets();
  if (!pairs.length || state.config.read_only) return;
  const single = pairs.length === 1;
  const category = single ? categoryClick(pairs[0].category, name).category : (pairs.every((pair) => pair.category === name) ? null : name);
  const focusedPair = state.unit.pairs[state.focused];
  const reasonSource = single ? pairs[0] : (pairs.includes(focusedPair) ? focusedPair : null);
  const justification = reasonSource ? reasonOf(reasonSource) : "";
  const cert = certaintyOf(single ? pairs[0] : focusedPair);
  const saved = await write("/api/label", labelRequest(state.unit.id, pairs, category, cert, justification),
    category ? `Saved ${category}${single ? "" : ` for ${pairs.length} pairs`}` : "Cleared");
  if (saved && single && category && state.focused < state.unit.pairs.length - 1) {
    moveFocus(1);
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
  const pair = state.unit.pairs.find((candidate) => pairKey(candidate) === key);
  if (!pair || !pair.category || !(key in state.reasons) || state.reasons[key] === pair.reason) return;
  await write("/api/label", labelRequest(state.unit.id, [pair], pair.category, certaintyOf(pair), state.reasons[key]), "Reason saved");
}

function moveFocus(delta) {
  const count = state.unit?.pairs.length || 0;
  if (!count) return;
  state.focused = Math.max(0, Math.min(count - 1, state.focused + delta));
  for (const panel of document.querySelectorAll(".pair")) {
    panel.classList.toggle("focused", Number(panel.dataset.index) === state.focused);
  }
  document.querySelector(`.pair[data-index="${state.focused}"]`)?.scrollIntoView({ block: "nearest" });
}

function toggleTick(key) {
  if (state.ticked.has(key)) state.ticked.delete(key);
  else state.ticked.add(key);
  render();
}

function togglePanel(id) {
  const panel = $(id);
  const show = panel.hidden;
  for (const other of ["help", "definitions"]) $(other).hidden = true;
  panel.hidden = !show;
  if (id === "definitions" && show) renderDefinitions();
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------

function setUpEvents() {
  $("pairs").addEventListener("click", (event) => {
    const panel = event.target.closest(".pair");
    if (!panel) return;
    const index = Number(panel.dataset.index);
    if (index !== state.focused) moveFocus(index - state.focused);
    const categoryButton = event.target.closest("[data-category]");
    if (categoryButton) return chooseCategory(categoryButton.dataset.category);
    const certButton = event.target.closest("[data-cert]");
    if (certButton) return chooseCertainty(certButton.dataset.cert);
  });
  $("pairs").addEventListener("change", (event) => {
    if (event.target.dataset.tick) toggleTick(event.target.dataset.tick);
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
      metaKey: event.metaKey, altKey: event.altKey, inTextField: event.target.matches("textarea, input, select"),
    }, state.config.buttons);
    if (!action) return;
    event.preventDefault();
    switch (action.type) {
      case "category": return chooseCategory(action.name);
      case "certainty": return chooseCertainty(action.value);
      case "move": return moveFocus(action.delta);
      case "toggle_tick": {
        const pair = state.unit?.pairs[state.focused];
        return pair && toggleTick(pairKey(pair));
      }
      case "next_unclassified": return goToUnit(state.unit?.next_unclassified);
      case "help": return togglePanel("help");
      case "leave_text": return document.activeElement?.blur();
    }
  });

  const onFilterChange = () => {
    state.filters = { sample: $("queue-sample").value, status: $("queue-status").value, verse: $("queue-verse").value.trim() };
    location.hash = `#/${filterQuery()}`;
  };
  $("queue-sample").addEventListener("change", onFilterChange);
  $("queue-status").addEventListener("change", onFilterChange);
  $("queue-verse").addEventListener("change", onFilterChange);
  $("help-toggle").addEventListener("click", () => togglePanel("help"));
  $("definitions-toggle").addEventListener("click", () => togglePanel("definitions"));
  window.addEventListener("hashchange", route);
}

async function start() {
  state.config = await api("/api/config");
  $("file").textContent = state.config.file + (state.config.read_only ? " (read-only)" : "");
  document.title = `rdgai annotate · ${state.config.file}`;
  if (!state.config.has_sample) {
    for (const value of ["sampled", "example", "test"]) $("queue-sample").querySelector(`option[value="${value}"]`).hidden = true;
  }
  setUpEvents();
  await route();
}

start().catch((error) => showStatus(error.message, true));

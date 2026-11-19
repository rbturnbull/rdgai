// Pure logic for the rdgai annotation GUI (no DOM access), tested in tests/js/logic.test.mjs.

const CERTAINTY_KEYS = { h: "high", m: "medium", l: "low" };

// Maps a key press to an action. `event` has key, code, shiftKey, ctrlKey, metaKey, altKey and inTextField.
// `buttons` are the category buttons from /api/config, each with a `key` such as "1", "0" or "shift+1".
export function keyAction(event, buttons) {
  if (event.inTextField) {
    return event.key === "Escape" ? { type: "leave_text" } : null;
  }
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  // annotation keys act on the unit being annotated; the other views are used with the mouse
  if (event.key === "t" && !event.shiftKey) return { type: "translation" };
  if (event.view && event.view !== "unit") return event.key === "?" ? { type: "help" } : null;

  const digit = /^Digit([0-9])$/.exec(event.code || "");
  if (digit) {
    const key = (event.shiftKey ? "shift+" : "") + digit[1];
    const button = buttons.find((candidate) => candidate.key === key);
    return button ? { type: "category", name: button.name } : null;
  }
  if (event.key === "?") return { type: "help" };
  if (event.shiftKey) return null;

  switch (event.key) {
    case "Enter":
    case "e": return { type: "expand" };
    case "j": return { type: "move", delta: 1 };
    case "k": return { type: "move", delta: -1 };
    case "n": return { type: "next_unclassified" };
    case "x": return { type: "toggle_tick" };
    default:
      if (event.key in CERTAINTY_KEYS) return { type: "certainty", value: CERTAINTY_KEYS[event.key] };
      return null;
  }
}

// Clicking the current category clears it; clicking another replaces it.
export function categoryClick(current, clicked) {
  return { category: current === clicked ? null : clicked };
}

export function pairKey(pair) {
  return `${pair.first}|${pair.second}`;
}

// The pairs an action applies to: the ticked pairs (in display order) if any, otherwise the focused pair.
export function targetPairs(pairs, focusedIndex, ticked) {
  if (ticked.size > 0) return pairs.filter((pair) => ticked.has(pairKey(pair)));
  return focusedIndex >= 0 && focusedIndex < pairs.length ? [pairs[focusedIndex]] : [];
}

// The categories the page last saw for these pairs; the server refuses the write if any has changed since.
export function expectedFor(pairs) {
  return Object.fromEntries(pairs.map((pair) => [pairKey(pair), pair.category ?? null]));
}

export function labelRequest(unitId, pairs, category, cert, justification, justificationSource = null) {
  const request = {
    unit_id: unitId,
    pairs: pairs.map((pair) => [pair.first, pair.second]),
    category,
    cert,
    justification,
    expected: expectedFor(pairs),
  };
  if (justificationSource) request.justification_source = justificationSource;
  return request;
}

export function certaintyRequest(unitId, pairs, cert) {
  return { unit_id: unitId, pairs: pairs.map((pair) => [pair.first, pair.second]), cert };
}

// Pairs that j/k move through: rule pairs only when their (collapsed) section is expanded.
export function visiblePairs(unit, showRules) {
  return [...(unit.pairs || []), ...(showRules ? unit.rule_pairs || [] : [])];
}

export function progressText(progress) {
  const text = `${progress.pairs_done} / ${progress.pairs_total} ${progress.pairs_total === 1 ? "pair" : "pairs"}`;
  return progress.rule_pairs ? `${text} · ${progress.rule_pairs} by rule` : text;
}

// Where a rule pair's label comes from.
export function ruleText(rule) {
  if (rule.rule === 1) return "By rule: subreading and parent";
  return "By rule: subreadings of the same reading";
}

// What to do when the file's version token has been checked: reload the unit if the file changed, but never while
// the page is hidden (check again when it is shown) or while the annotator is typing (check again later).
export function refreshAction({ hidden, typing, known, latest }) {
  if (known === null || known === undefined) return "remember";
  if (known === latest) return "none";
  if (hidden) return "skip";
  if (typing) return "later";
  return "reload";
}

// Whose wording a reason is: the source it was copied from, but only if it is saved exactly as copied.
export function justificationSource(reason, copied) {
  if (!copied || !reason || !reason.trim()) return null;
  return reason.trim() === copied.text.trim() ? copied.source : null;
}

// Labels and suggestions saved for the reverse direction start "Inverse of a → b: "; that is not shown.
export function displayJustification(text) {
  return (text || "").replace(/^Inverse of \S+ → \S+:\s*/, "");
}

// Units with more pairs than the threshold are shown as compact rows.
export function isCompact(unit, threshold) {
  return (unit.pairs?.length || 0) + (unit.rule_pairs?.length || 0) > threshold;
}

// Consecutive rows with the same first reading, in display order.
export function groupByFirst(pairs) {
  const groups = [];
  for (const pair of pairs) {
    const last = groups[groups.length - 1];
    if (last && last.first === pair.first) last.pairs.push(pair);
    else groups.push({ first: pair.first, pairs: [pair] });
  }
  return groups;
}

// Keys of the pairs whose (first) suggestion is the same category as the given pair's.
export function sameSuggestion(pairs, pair) {
  const category = pair.suggestions?.[0]?.category;
  if (!category) return [];
  return pairs.filter((candidate) => candidate.suggestions?.[0]?.category === category).map(pairKey);
}

// Keys of the pairs whose computed change is described the same way as the given pair's.
export function sameChange(pairs, pair) {
  const text = pair.change?.text;
  if (!text) return [];
  return pairs.filter((candidate) => candidate.change?.text === text).map(pairKey);
}

// ---------------------------------------------------------------------------
// views and routing: #/unit/ID?filters, #/category/NAME?params, #/agreement?params, otherwise the queue's start
// ---------------------------------------------------------------------------

export const QUEUE_FILTERS = ["sample", "status", "verse", "category", "cert", "source", "disagreement"];

function query(values) {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(values || {})) if (value !== "" && value !== null && value !== undefined && value !== 0) params.set(name, value);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function parseRoute(hash) {
  const [path, search = ""] = (hash || "").replace(/^#/, "").split("?");
  const params = Object.fromEntries(new URLSearchParams(search));
  const filters = Object.fromEntries(QUEUE_FILTERS.map((name) => [name, params[name] || ""]));
  let match = /^\/unit\/(.+)$/.exec(path);
  if (match) return { view: "unit", unitId: decodeURIComponent(match[1]), filters };
  match = /^\/category\/(.+)$/.exec(path);
  if (match) return { view: "category", category: decodeURIComponent(match[1]), params };
  if (path === "/agreement") return { view: "agreement", params };
  return { view: "queue", filters };
}

export function routeHash(route) {
  switch (route.view) {
    case "unit": return `#/unit/${encodeURIComponent(route.unitId)}${query(route.filters)}`;
    case "category": return `#/category/${encodeURIComponent(route.category)}${query(route.params)}`;
    case "agreement": return `#/agreement${query(route.params)}`;
    default: return `#/${query(route.filters)}`;
  }
}

// Which rows a page shows (1-based, for display) and the offsets of the previous and next pages.
export function pageInfo(total, offset, limit) {
  if (!total) return { from: 0, to: 0, prev: null, next: null };
  return {
    from: offset + 1,
    to: Math.min(offset + limit, total),
    prev: offset > 0 ? Math.max(offset - limit, 0) : null,
    next: offset + limit < total ? offset + limit : null,
  };
}

// ---------------------------------------------------------------------------
// translations: the words in which the translations of two readings differ
// ---------------------------------------------------------------------------

// Splits a text into words and the spaces between them; words are compared without case or punctuation.
function wordParts(text) {
  return (text || "").split(/(\s+)/).filter((part) => part !== "").map((part) => ({ text: part, word: /\S/.test(part) }));
}

const wordKey = (text) => text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

// Marks the words of each translation that are not in their longest common subsequence.
export function diffWords(firstText, secondText) {
  const first = wordParts(firstText);
  const second = wordParts(secondText);
  const a = first.filter((part) => part.word);
  const b = second.filter((part) => part.word);
  const lengths = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lengths[i][j] = wordKey(a[i].text) === wordKey(b[j].text) ? lengths[i + 1][j + 1] + 1 : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
    }
  }
  const keptA = new Set();
  const keptB = new Set();
  for (let i = 0, j = 0; i < a.length && j < b.length;) {
    if (wordKey(a[i].text) === wordKey(b[j].text)) { keptA.add(i++); keptB.add(j++); }
    else if (lengths[i + 1][j] >= lengths[i][j + 1]) i++;
    else j++;
  }
  const mark = (parts, kept) => {
    let index = -1;
    return parts.map((part) => {
      if (!part.word) return { text: part.text, changed: false };
      index += 1;
      return { text: part.text, changed: !kept.has(index) };
    });
  };
  return { first: mark(first, keptA), second: mark(second, keptB) };
}

// The parts of a translation with the words that render the reading marked ⸂…⸃ (and ⸆ where an omission falls);
// null if the translation has no marks.
export function markedParts(text) {
  if (!text || !/[⸂⸃⸆]/.test(text)) return null;
  const parts = [];
  let marked = false;
  for (const piece of text.split(/([⸂⸃⸆])/)) {
    if (piece === "⸂") marked = true;
    else if (piece === "⸃") marked = false;
    else if (piece === "⸆") parts.push({ text: "⸆", marked: true, omission: true });
    else if (piece) parts.push({ text: piece, marked });
  }
  return parts;
}

// Each pair's rows show the verse and both readings, so the overview above the pairs is shown only for compact units,
// whose one-line rows do not.
export function showsOverview(unit, threshold) {
  return isCompact(unit, threshold);
}

// What the header shows for the annotator: the short name in the circle, and the full name beside it unless it is
// the same (so "RT" is not shown twice).
export function headerUser(profile) {
  if (!profile || !profile.id) return null;
  const short = profile.short_name || profile.id;
  const name = profile.name && profile.name !== short ? profile.name : null;
  return { name, short };
}

// "second reading omits: και" -> { label: "second reading omits", words: "και" }, so the words can stand out.
export function changeParts(text) {
  const index = (text || "").indexOf(": ");
  if (index < 0) return { label: text || "", words: "" };
  return { label: text.slice(0, index), words: text.slice(index + 2) };
}

// "1 pair", "3 pairs" (or a given plural, e.g. witnesses).
export function countOf(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

// "Use reason" is offered for any suggestion with a reason (not when read-only), whichever direction it was written for
export function canUseReason(suggestion, readOnly) {
  return Boolean(suggestion?.justification) && !readOnly;
}

// the reason as copied into the reason box: a reason written for the other direction says so, so it isn't saved as
// though it described the pair as shown (where e.g. "adds" would read as the opposite)
export function usableReason(suggestion) {
  const text = suggestion.justification || "";
  return suggestion.written_for ? `Written for ${suggestion.written_for[0]} → ${suggestion.written_for[1]}: ${text}` : text;
}

// The pair rail beside the pairs: one entry per pair shown ("a → b"), in display order, marking the current pair,
// the labelled ones, and those labelled by a subreading rule.
export function pairNavItems(pairs, focusedIndex) {
  return pairs.map((pair, index) => ({
    index,
    label: `${pair.first} → ${pair.second}`,
    current: index === focusedIndex,
    done: Boolean(pair.category),
    rule: pair.source === "rule",
    category: pair.category || null,
  }));
}

// "Next to do": the next unit with pairs still to label. Once every pair in this unit is labelled it is "ready",
// and the rail shows it as the main action.
export function nextToDo(unit) {
  const target = unit.next_unclassified || null;
  const progress = unit.progress || {};
  return { target, ready: Boolean(target) && progress.pairs_done >= progress.pairs_total };
}

// A category button's tooltip: its name, key and inverse. Not the definition, which is long and is in the
// Definitions panel.
export function categoryTitle(button) {
  const key = button.key ? ` (key ${button.key.replace("shift+", "⇧")})` : "";
  const inverse = button.inverse && button.inverse !== button.name ? ` · inverse: ${button.inverse}` : "";
  return `${button.name}${key}${inverse}`;
}

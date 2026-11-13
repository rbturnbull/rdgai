// Pure logic for the rdgai annotation GUI (no DOM access), tested in tests/js/logic.test.mjs.

const CERTAINTY_KEYS = { h: "high", m: "medium", l: "low" };

// Maps a key press to an action. `event` has key, code, shiftKey, ctrlKey, metaKey, altKey and inTextField.
// `buttons` are the category buttons from /api/config, each with a `key` such as "1", "0" or "shift+1".
export function keyAction(event, buttons) {
  if (event.inTextField) {
    return event.key === "Escape" ? { type: "leave_text" } : null;
  }
  if (event.ctrlKey || event.metaKey || event.altKey) return null;

  const digit = /^Digit([0-9])$/.exec(event.code || "");
  if (digit) {
    const key = (event.shiftKey ? "shift+" : "") + digit[1];
    const button = buttons.find((candidate) => candidate.key === key);
    return button ? { type: "category", name: button.name } : null;
  }
  if (event.key === "?") return { type: "help" };
  if (event.shiftKey) return null;

  switch (event.key) {
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

export function labelRequest(unitId, pairs, category, cert, justification) {
  return {
    unit_id: unitId,
    pairs: pairs.map((pair) => [pair.first, pair.second]),
    category,
    cert,
    justification,
    expected: expectedFor(pairs),
  };
}

export function certaintyRequest(unitId, pairs, cert) {
  return { unit_id: unitId, pairs: pairs.map((pair) => [pair.first, pair.second]), cert };
}

"""
The GUI in a real browser (headless Chrome, driven over the DevTools protocol): what the annotator actually sees.

Regression test for stale reading cards: after "Next" from a unit whose reading cards are shown above the pairs
(a compact unit, with more than 10 pairs) to one without them, the previous unit's cards stayed on screen, because
a CSS display rule overrode the section's ``hidden`` attribute and the old cards were never removed.

Skipped if Chrome or the ``websockets`` package is not available.
"""
import asyncio
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from .util import TEST_DATA_DIR

websockets = pytest.importorskip("websockets")

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]
CHROME = next((path for path in CHROME_CANDIDATES if path and Path(path).exists()), None)
RDGAI = str(Path(sys.executable).with_name("rdgai"))

pytestmark = pytest.mark.skipif(CHROME is None or not Path(RDGAI).exists(), reason="Chrome or the rdgai entry point is not available")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url:str, seconds:float=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url) as response:
                return json.load(response)
        except Exception:
            time.sleep(0.2)
    raise AssertionError(f"{url} did not respond")


# the first Chrome launch on a fresh CI runner (font cache, new profile) can take well over 15 seconds
CHROME_STARTUP_SECONDS = 60


def wait_for_chrome(chrome:subprocess.Popen, port_file:Path, log:Path, seconds:float=CHROME_STARTUP_SECONDS) -> int:
    """ Waits for Chrome to write its DevTools port, failing at once (with Chrome's stderr) if it exits first. """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if chrome.poll() is not None:
            raise AssertionError(f"Chrome exited with code {chrome.returncode}:\n{log.read_text(errors='replace')}")
        try:
            lines = port_file.read_text().splitlines()
            if lines and lines[0].strip().isdigit():
                return int(lines[0])
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"Chrome did not open a DevTools port within {seconds}s:\n{log.read_text(errors='replace')}")


class Page:
    """ A minimal DevTools-protocol client: navigate and evaluate JavaScript. """
    def __init__(self, ws):
        self.ws, self.next_id = ws, 0

    async def send(self, method, **params):
        self.next_id += 1
        await self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.next_id:
                return message.get("result", {})

    async def eval(self, expression):
        result = await self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        return result.get("result", {}).get("value")

    async def wait_until(self, expression, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if await self.eval(expression):
                return
            await asyncio.sleep(0.1)
        raise AssertionError(f"timed out waiting for: {expression}")


# the reading cards the annotator can see: rendered, and not inside a hidden ancestor
VISIBLE_TOP_CARDS = """
[...document.querySelectorAll('#readings .reading-card')]
  .filter((card) => card.getClientRects().length > 0)
  .map((card) => card.querySelector('.reading-id').textContent.split(' ')[0] + ':' + card.querySelector('.reading-text').firstChild.textContent)
"""


@pytest.fixture
def prepare():
    """ Changes made to the copied file (through an AnnotationStore) before the server starts: none by default. """
    return None


@pytest.fixture
def gui(tmp_path, prepare):
    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    if prepare:
        from rdgai.annotation import AnnotationStore
        prepare(AnnotationStore(path, annotator="RT"))
    port = free_port()
    server = subprocess.Popen([RDGAI, "annotate", str(path), "--port", str(port), "--annotator", "RT"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome_log = tmp_path / "chrome.log"
    with open(chrome_log, "wb") as log:
        # port 0: Chrome picks a free port itself and writes it to DevToolsActivePort (no race with free_port)
        chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--remote-debugging-port=0",
                                   f"--user-data-dir={tmp_path / 'chrome'}", "--window-size=1400,1000", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=log)
    try:
        wait_for(f"http://127.0.0.1:{port}/api/config")
        debug_port = wait_for_chrome(chrome, tmp_path / "chrome" / "DevToolsActivePort", chrome_log)
        targets = wait_for(f"http://127.0.0.1:{debug_port}/json")
        yield f"http://127.0.0.1:{port}", next(target for target in targets if target["type"] == "page")["webSocketDebuggerUrl"]
    finally:
        chrome.kill()
        server.kill()
        chrome.wait()
        server.wait()


def unit_readings(base:str, unit_id:str) -> list[str]:
    unit = wait_for(f"{base}/api/unit/{unit_id}?sample=all")
    return [f"{reading['id']}:{reading['text']}" for reading in unit["readings"]]


def test_next_replaces_the_reading_cards(gui):
    base, ws_url = gui
    compact, simple = "Mark.1.1.12-16", "Mark.1.2.2"   # 6 readings (15 pairs: cards shown), then 2 readings
    assert len(unit_readings(base, compact)) == 6
    assert wait_for(f"{base}/api/unit/{compact}?sample=all")["next"] == simple

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{compact}?sample=all")
            await page.wait_until(f"document.querySelector('#header-unit h1')?.textContent === '{compact}'")
            await page.wait_until("document.querySelectorAll('#readings .reading-card').length > 0")
            assert sorted(await page.eval(VISIBLE_TOP_CARDS)) == sorted(unit_readings(base, compact))

            # Next →
            await page.eval("[...document.querySelectorAll('#pair-nav .unit-nav button')].find((b) => b.textContent.startsWith('Next →')).click()")
            await page.wait_until(f"document.querySelector('#header-unit h1')?.textContent === '{simple}'")
            await asyncio.sleep(0.5)
            shown = await page.eval(VISIBLE_TOP_CARDS)
            # no card from the previous unit may be visible; any card shown belongs to this unit
            assert set(shown) <= set(unit_readings(base, simple)), shown
            # the cards (where shown) are marked with the unit they belong to, matching the header
            marked = await page.eval("document.getElementById('readings').dataset.unit || null")
            assert marked in (None, simple)

            # and back: the compact unit's cards are shown again, all of them
            await page.eval("[...document.querySelectorAll('#pair-nav .unit-nav button')].find((b) => b.textContent.startsWith('← Prev')).click()")
            await page.wait_until(f"document.querySelector('#header-unit h1')?.textContent === '{compact}'")
            await asyncio.sleep(0.5)
            assert sorted(await page.eval(VISIBLE_TOP_CARDS)) == sorted(unit_readings(base, compact))
            assert await page.eval("document.getElementById('readings').dataset.unit") == compact

    asyncio.run(run())


def test_hidden_always_wins_in_the_stylesheet():
    """ A display rule (e.g. .readings { display: flow-root }) must never override the hidden attribute. """
    css = (Path(__file__).parents[1] / "rdgai" / "gui" / "static" / "style.css").read_text()
    assert "[hidden] { display: none !important; }" in css


def test_expanding_a_compact_row(gui):
    """
    In a compact unit, a row expands with a disclosure triangle at its top left (in the same place whether collapsed
    or expanded), and the expanded row's box contains everything: the reading cards and verse as well as the decision.
    """
    base, ws_url = gui
    compact = "Mark.1.1.12-16"

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{compact}?sample=all")
            await page.wait_until("document.querySelectorAll('.pair.compact').length > 0")
            first = "document.querySelector('#pairs .pair[data-index=\"0\"]')"
            # no More/Less text buttons: a disclosure triangle, the row's first element, collapsed
            assert await page.eval(f"[...document.querySelectorAll('#pairs button')].filter((b) => /^(More|Less)$/.test(b.textContent.trim())).length") == 0
            assert await page.eval(f"{first}.firstElementChild.matches('button.disclosure')")
            assert await page.eval(f"{first}.querySelector('button.disclosure').getAttribute('aria-expanded')") == "false"
            collapsed = await page.eval(f"(() => {{ const r = {first}.querySelector('button.disclosure').getBoundingClientRect(), p = {first}.getBoundingClientRect(); return [Math.round(r.left - p.left), Math.round(r.top - p.top)]; }})()")

            await page.eval(f"{first}.querySelector('button.disclosure').click()")
            await page.wait_until(f"{first}.classList.contains('expanded')")
            assert await page.eval(f"{first}.querySelector('button.disclosure').getAttribute('aria-expanded')") == "true"
            assert await page.eval(f"{first}.firstElementChild.matches('button.disclosure, .expanded-head')")
            expanded = await page.eval(f"(() => {{ const r = {first}.querySelector('button.disclosure').getBoundingClientRect(), p = {first}.getBoundingClientRect(); return [Math.round(r.left - p.left), Math.round(r.top - p.top)]; }})()")
            assert abs(collapsed[0] - expanded[0]) <= 2 and abs(collapsed[1] - expanded[1]) <= 6, (collapsed, expanded)
            # the box (its border) contains the cards, the verse and the decision
            assert await page.eval(f"getComputedStyle({first}).borderTopStyle") != "none"
            assert await page.eval(f"!!{first}.querySelector('.pair-readings .reading-card') && !!{first}.querySelector('.decision .categories')")

            # and collapses again
            await page.eval(f"{first}.querySelector('button.disclosure').click()")
            await page.wait_until(f"!{first}.classList.contains('expanded')")
            assert await page.eval(f"{first}.querySelector('button.disclosure').getAttribute('aria-expanded')") == "false"

    asyncio.run(run())


def test_clicking_a_button_does_not_scroll(gui):
    """
    Clicking a button (here a category, which saves the label and moves the focus to the next pair) must leave the
    page where it is: neither focusing the clicked pair nor moving on to the next one may scroll the page.
    """
    base, ws_url = gui
    unit = "Mark.1.2.8-14"   # 4 readings: 6 full pair panels, taller than the window

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{unit}?sample=all")
            await page.wait_until("document.querySelectorAll('#pairs .pair.full').length >= 3")
            second = "document.querySelector('#pairs .pair[data-index=\"1\"]')"
            # scroll so the second pair's top is just above the window: it is not fully visible, so
            # scrollIntoView would move the page; its category buttons are on screen
            await page.eval(f"window.scrollTo(0, window.scrollY + {second}.getBoundingClientRect().top + 40)")
            await asyncio.sleep(0.3)
            before = await page.eval("window.scrollY")
            assert before > 0
            button = f"{second}.querySelector('.decision [data-category]')"
            top = await page.eval(f"{button}.getBoundingClientRect().top")
            assert 0 < top < await page.eval("window.innerHeight")
            await page.eval(f"{button}.click()")
            await page.wait_until(f"!!{second}.querySelector('.decision [data-category].current')")
            await asyncio.sleep(0.5)
            assert abs(await page.eval("window.scrollY") - before) <= 2

    asyncio.run(run())


REASON_UNIT = "Mark.1.2.8-14"


def suggest_and_label(store):
    # a -> b (shown that way): labelled, with a suggestion; a -> c: a suggestion only; b -> c: labelled
    store.suggest_classification(REASON_UNIT, "a", "b", "Minor_Omission", justification="omits a word", source="claude")
    store.set_classification(REASON_UNIT, "a", "b", "Minor_Omission", justification="")
    store.set_classification(REASON_UNIT, "b", "c", "Major_Substitution", justification="")
    store.suggest_classification(REASON_UNIT, "a", "c", "Minor_Omission", justification="omits another word", source="claude")


def saved_pair(base:str, first:str, second:str) -> dict:
    unit = wait_for(f"{base}/api/unit/{REASON_UNIT}?sample=all")
    return next(pair for pair in unit["pairs"] if (pair["first"], pair["second"]) == (first, second))


@pytest.mark.parametrize("prepare", [suggest_and_label])
def test_use_reason_saves_on_a_labelled_pair(gui):
    """ On a pair that already has its label, "Use reason" saves the reason straight away (as the suggester's wording). """
    base, ws_url = gui
    assert saved_pair(base, "a", "b")["reason"] == ""

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{REASON_UNIT}?sample=all")
            panel = "[...document.querySelectorAll('#pairs .pair.full')].find((p) => p.querySelector('.copy-reason') && p.textContent.includes('omits a word'))"
            await page.wait_until(f"!!({panel})")
            await page.eval(f"({panel}).querySelector('.copy-reason').click()")
            deadline = time.time() + 5
            while time.time() < deadline and saved_pair(base, "a", "b")["reason"] != "omits a word":
                await asyncio.sleep(0.2)

    asyncio.run(run())
    pair = saved_pair(base, "a", "b")
    assert pair["reason"] == "omits a word"
    assert pair["category"] == "Minor_Omission"   # the label is kept


@pytest.mark.parametrize("prepare", [suggest_and_label])
def test_use_reason_waits_for_the_category_on_an_unlabelled_pair(gui):
    """ Without a label there is nothing to save yet: the reason is saved with the category chosen next. """
    base, ws_url = gui

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{REASON_UNIT}?sample=all")
            panel = "[...document.querySelectorAll('#pairs .pair.full')].find((p) => p.querySelector('.copy-reason') && p.textContent.includes('omits another word'))"
            await page.wait_until(f"!!({panel})")
            await page.eval(f"({panel}).querySelector('.copy-reason').click()")
            await asyncio.sleep(0.5)
            assert saved_pair(base, "a", "c")["category"] is None
            # the annotator is told the reason is waiting for a category
            assert "category" in (await page.eval("document.getElementById('status').textContent")).lower()
            await page.eval(f"({panel}).querySelector('.decision [data-category=Minor_Omission]').click()")
            deadline = time.time() + 5
            while time.time() < deadline and saved_pair(base, "a", "c")["category"] is None:
                await asyncio.sleep(0.2)

    asyncio.run(run())
    pair = saved_pair(base, "a", "c")
    assert (pair["category"], pair["reason"]) == ("Minor_Omission", "omits another word")


def test_pair_rail_shows_where_the_annotator_is(gui):
    """
    A narrow rail beside the pairs holds the unit's progress line (moved from the header) and one entry per pair,
    "a → b", with the current pair highlighted. Clicking an entry goes to that pair; moving with the keyboard
    moves the highlight.
    """
    base, ws_url = gui
    unit = "Mark.1.2.8-14"

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{unit}?sample=all")
            await page.wait_until("document.querySelectorAll('#pairs .pair.full').length === 6")
            await page.wait_until("document.querySelectorAll('#pair-nav .pair-nav-item').length === 6")
            # the progress line is in the rail, not the header
            assert "0 / 6 pairs" in await page.eval("document.getElementById('pair-nav').textContent")
            assert "pairs" not in await page.eval("document.getElementById('header-unit').textContent")
            labels = await page.eval("[...document.querySelectorAll('#pair-nav .pair-nav-item')].map((item) => item.querySelector('.pair-nav-ids').textContent)")
            shown = await page.eval("[...document.querySelectorAll('#pairs .pair.full')].map((pair) => pair.dataset.index)")
            assert labels[0] == "a → b" and len(labels) == len(shown)
            current = "[...document.querySelectorAll('#pair-nav .pair-nav-item')].findIndex((item) => item.classList.contains('current'))"
            assert await page.eval(current) == 0
            # beside the pairs, not over them
            assert await page.eval("document.getElementById('pair-nav').getBoundingClientRect().left >= document.getElementById('pairs').getBoundingClientRect().right")

            # clicking an entry goes to that pair
            await page.eval("document.querySelectorAll('#pair-nav .pair-nav-item')[3].click()")
            await page.wait_until(f"{current} === 3")
            assert await page.eval("document.querySelector('#pairs .pair[data-index=\"3\"]').classList.contains('focused')")
            # and scrolls it into view, at the top below the header
            await page.wait_until("(() => { const top = document.querySelector('#pairs .pair[data-index=\"3\"]').getBoundingClientRect().top; return top >= 0 && top < 200; })()")

            # the keyboard moves the highlight too
            await page.send("Input.dispatchKeyEvent", type="keyDown", key="j", code="KeyJ", text="j", windowsVirtualKeyCode=74)
            await page.send("Input.dispatchKeyEvent", type="keyUp", key="j", code="KeyJ", windowsVirtualKeyCode=74)
            await page.wait_until(f"{current} === 4")

    asyncio.run(run())


def test_pair_rail_at_the_right_edge_and_pairs_centred(gui):
    """
    The rail sits at the right edge of the window; the pairs are centred in the main area (beside the left sidebar)
    when there is room, and never run under the rail. The labelled mark stands out from the entry's text.
    """
    base, ws_url = gui
    unit = "Mark.1.2.8-14"

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Emulation.setDeviceMetricsOverride", width=2000, height=1000, deviceScaleFactor=1, mobile=False)
            await page.send("Page.navigate", url=f"{base}/#/unit/{unit}?sample=all")
            await page.wait_until("document.querySelectorAll('#pair-nav .pair-nav-item').length === 6")
            await asyncio.sleep(0.3)
            geometry = await page.eval("""(() => {
                const main = document.getElementById('main').getBoundingClientRect();
                const body = document.querySelector('#view-unit .unit-body').getBoundingClientRect();
                const rail = document.getElementById('pair-nav').getBoundingClientRect();
                return { mainLeft: main.left, mainRight: main.right, bodyLeft: body.left, bodyRight: body.right,
                         railLeft: rail.left, railRight: rail.right, width: document.documentElement.clientWidth };
            })()""")
            # the rail at the right edge
            assert geometry["width"] - geometry["railRight"] <= 24, geometry
            # the pairs centred in the main area, clear of the rail
            centre = (geometry["mainLeft"] + geometry["mainRight"]) / 2
            assert abs((geometry["bodyLeft"] + geometry["bodyRight"]) / 2 - centre) <= 2, geometry
            assert geometry["bodyRight"] <= geometry["railLeft"], geometry

            # narrower: the pairs move left to make room, still clear of the rail
            await page.send("Emulation.setDeviceMetricsOverride", width=1400, height=1000, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.3)
            assert await page.eval("document.querySelector('#view-unit .unit-body').getBoundingClientRect().right <= document.getElementById('pair-nav').getBoundingClientRect().left")

    asyncio.run(run())


@pytest.mark.parametrize("prepare", [suggest_and_label])
def test_pair_rail_check_mark_stands_out(gui):
    """ A labelled entry's ✓ is a badge: bold, and on its own background, unlike the entry's text. """
    base, ws_url = gui

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{REASON_UNIT}?sample=all")
            done = "document.querySelector('#pair-nav .pair-nav-item.done:not(.current)')"
            await page.wait_until(f"!!{done}")
            mark = await page.eval(f"(() => {{ const s = getComputedStyle({done}.querySelector('.pair-nav-mark')); return [s.backgroundColor, s.fontWeight]; }})()")
            item = await page.eval(f"getComputedStyle({done}).backgroundColor")
            assert mark[0] not in ("rgba(0, 0, 0, 0)", "transparent", item), mark
            assert int(mark[1]) >= 700
            # with room between the ✓ and the pair's ids
            gap = await page.eval(f"{done}.querySelector('.pair-nav-ids').getBoundingClientRect().left - {done}.querySelector('.pair-nav-mark').getBoundingClientRect().right")
            assert gap >= 8, gap

    asyncio.run(run())


# delays the responses for one unit, to make the order in which loads finish deterministic
DELAY_UNIT = """
(() => {
  const original = window.fetch;
  window.__delay = { unit: null, ms: 0 };
  window.fetch = async (path, options) => {
    const response = await original(path, options);
    if (window.__delay.unit && String(path).startsWith(`/api/unit/${window.__delay.unit}`)) {
      await new Promise((resolve) => setTimeout(resolve, window.__delay.ms));
    }
    return response;
  };
})()
"""

HEADER_UNIT = "document.querySelector('#header-unit h1')?.textContent"


def press(page, key, code, keycode):
    async def run():
        await page.send("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, text=key, windowsVirtualKeyCode=keycode)
        await page.send("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=keycode)
    return run()


def test_next_is_not_undone_by_a_slower_reload(gui):
    """
    Labelling a pair reloads the unit; clicking Next before that reload returns must still move on. A load that
    finishes after a newer one must not put the previous unit back on screen.
    """
    base, ws_url = gui
    first, second = "Mark.1.2.8-14", wait_for(f"{base}/api/unit/Mark.1.2.8-14?sample=all")["next"]

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{first}?sample=all")
            await page.wait_until(f"{HEADER_UNIT} === '{first}' && document.querySelectorAll('#pairs .pair.full').length > 0")
            await page.eval(DELAY_UNIT)
            await page.eval(f"window.__delay = {{ unit: '{first}', ms: 1500 }}")
            # label the first pair (its reload of this unit is slow), then Next straight away
            await page.eval("document.querySelector('#pairs .pair[data-index=\"0\"] .decision [data-category]').click()")
            await asyncio.sleep(0.2)
            await page.eval("[...document.querySelectorAll('#pair-nav .unit-nav button')].find((b) => b.textContent.startsWith('Next →')).click()")
            await page.wait_until(f"{HEADER_UNIT} === '{second}'")
            await asyncio.sleep(2.5)   # the slow reload of the first unit has come back by now
            assert await page.eval(HEADER_UNIT) == second
            assert second in await page.eval("location.hash")

    asyncio.run(run())


def test_a_reload_during_next_reloads_the_unit_being_opened(gui):
    """ A reload started while Next is still loading (e.g. after a save) is for the unit being opened, not the old one. """
    base, ws_url = gui
    first, second = "Mark.1.2.8-14", wait_for(f"{base}/api/unit/Mark.1.2.8-14?sample=all")["next"]

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{first}?sample=all")
            await page.wait_until(f"{HEADER_UNIT} === '{first}' && document.querySelectorAll('#pairs .pair.full').length > 0")
            await page.eval(DELAY_UNIT)
            await page.eval(f"window.__delay = {{ unit: '{second}', ms: 1500 }}")
            # Next (slow), then a label on the pair still on screen, whose save reloads the unit
            await page.eval("[...document.querySelectorAll('#pair-nav .unit-nav button')].find((b) => b.textContent.startsWith('Next →')).click()")
            await asyncio.sleep(0.2)
            await press(page, "1", "Digit1", 49)
            await asyncio.sleep(3.5)
            assert await page.eval(HEADER_UNIT) == second
            assert second in await page.eval("location.hash")

    asyncio.run(run())


def test_unit_navigation_at_the_top_of_the_rail(gui):
    """
    Prev and Next are at the top of the rail, not in the header; under them "Next to do", which becomes the main
    (filled) button once every pair in the unit is labelled.
    """
    base, ws_url = gui
    unit = "Mark.1.2.2"   # one pair

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{unit}?sample=all")
            await page.wait_until(f"{HEADER_UNIT} === '{unit}' && !!document.querySelector('#pair-nav .unit-nav')")
            assert await page.eval("document.querySelectorAll('#header-unit button').length") == 0
            assert await page.eval("document.getElementById('pair-nav').firstElementChild.classList.contains('unit-nav')")
            texts = await page.eval("[...document.querySelectorAll('#pair-nav .unit-nav button')].map((b) => b.textContent.trim())")
            assert texts[0].startswith("←") and texts[1].endswith("→") and texts[2].startswith("Next to do"), texts
            todo = "document.querySelector('#pair-nav .unit-nav .next-to-do')"
            assert not await page.eval(f"{todo}.classList.contains('ready')")

            # label the only pair: the unit is done, and "Next to do" becomes the main action
            await page.eval("document.querySelector('#pairs .pair[data-index=\"0\"] .decision [data-category]').click()")
            await page.wait_until(f"{todo}.classList.contains('ready')")
            target = await page.eval(f"{todo}.dataset.target")
            await page.eval(f"{todo}.click()")
            await page.wait_until(f"{HEADER_UNIT} === '{target}'")

    asyncio.run(run())


def test_rail_buttons_survive_a_redraw_during_a_click(gui):
    """
    A click is a press and a release on the same element. Saving a label redraws the page (twice: at once, and when
    the unit is reloaded), so if the rail's buttons were rebuilt, a click made just after labelling — "Next to do"
    as soon as the unit is done — would be lost. The buttons must stay the same elements across redraws.
    """
    base, ws_url = gui
    first = "Mark.1.2.8-14"

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{first}?sample=all")
            await page.wait_until(f"{HEADER_UNIT} === '{first}' && !!document.querySelector('#pair-nav .next-to-do:not(:disabled)')")
            todo = "document.querySelector('#pair-nav .next-to-do')"
            target = await page.eval(f"{todo}.dataset.target")
            await page.eval(f"{todo}.dataset.original = 'yes'")
            x, y = await page.eval(f"(() => {{ const r = {todo}.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")

            # press on "Next to do", save a label (which redraws), then release
            await page.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            await page.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
            await press(page, "1", "Digit1", 49)
            await page.wait_until("document.querySelector('#pairs .pair[data-index=\"0\"] .decision [data-category].current') !== null")
            await asyncio.sleep(0.8)   # the reload after the save has redrawn the page too
            assert await page.eval(f"{todo}.dataset.original") == "yes"   # the same button, not a new one
            await page.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
            await page.wait_until(f"{HEADER_UNIT} === '{target}'")

    asyncio.run(run())


def suggest_three(store):
    # a -> b and a -> c: suggestions only; a -> d: labelled by the annotator, with a suggested reason for the same category
    store.suggest_classification(REASON_UNIT, "a", "b", "Minor_Omission", justification="omits a word", source="claude")
    store.suggest_classification(REASON_UNIT, "a", "c", "Major_Substitution", justification="other words", source="claude")
    store.suggest_classification(REASON_UNIT, "a", "e", "Major_Substitution", justification="changed", source="claude")
    store.set_classification(REASON_UNIT, "a", "e", "Major_Substitution", justification="")


@pytest.mark.parametrize("prepare", [suggest_three])
def test_save_all_suggestions_and_reasons_buttons(gui):
    """ The buttons above the pairs save the unit's suggestions (after a confirmation) and update their counts. """
    base, ws_url = gui

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{REASON_UNIT}?sample=all")
            labels, reasons = "document.querySelector('#pair-nav .save-labels')", "document.querySelector('#pair-nav .save-reasons')"
            await page.wait_until(f"!!{labels} && {labels}.getClientRects().length > 0")
            assert await page.eval(f"{labels}.textContent") == "Save all (2)"
            assert await page.eval(f"{reasons}.textContent") == "Save reasons (1)"
            assert await page.eval("!document.getElementById('suggestion-bar')")   # no longer above the pairs
            await page.eval("window.confirm = () => true")

            await page.eval(f"{labels}.click()")
            await page.wait_until(f"{labels}.textContent === 'Save all (0)'")
            assert await page.eval(f"{labels}.disabled")
            await page.eval(f"{reasons}.click()")
            await page.wait_until(f"{reasons}.textContent === 'Save reasons (0)'")

    asyncio.run(run())
    assert (saved_pair(base, "a", "b")["category"], saved_pair(base, "a", "b")["reason"]) == ("Minor_Omission", "omits a word")
    assert saved_pair(base, "a", "c")["category"] == "Major_Substitution"
    assert saved_pair(base, "a", "e")["reason"] == "changed"


def test_suggestion_buttons_always_shown(gui):
    """ On a unit without suggestions the buttons are still there (greyed out), so they can be found. """
    base, ws_url = gui

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{REASON_UNIT}?sample=all")
            await page.wait_until("!!document.querySelector('#pair-nav .save-labels')")
            assert await page.eval("document.querySelector('#pair-nav .save-labels').getClientRects().length > 0")
            assert await page.eval("document.querySelector('#pair-nav .save-labels').disabled")
            assert await page.eval("document.querySelector('#pair-nav .save-reasons').disabled")
            assert "no suggestions" in (await page.eval("document.querySelector('#pair-nav .rail-suggestions').textContent")).lower()

    asyncio.run(run())


def draw_a_sample(store):
    store.draw_sample(size=10, seed=3)


@pytest.mark.parametrize("prepare", [draw_a_sample])
def test_sample_progress_bars(gui):
    """ The sidebar shows the sample's progress, for the roles the Units filter selects: pairs and percentage (1 dp). """
    base, ws_url = gui
    progress = wait_for(f"{base}/api/sample-progress")
    first_example = wait_for(f"{base}/api/queue?sample=example")["units"][0]["id"]

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            rows = "[...document.querySelectorAll('#sample-progress .sample-progress-row')]"
            await page.send("Page.navigate", url=f"{base}/#/unit/{first_example}?sample=sampled")
            await page.wait_until(f"{rows}.length === 3")
            texts = await page.eval(f"{rows}.map((row) => row.textContent)")
            example = progress["example"]
            assert texts[0].startswith(f"Example pairs ({example['pairs_done']}/{example['pairs_total']})0.0%"), texts
            assert f"Example units 0/{example['units_total']} (0.0%)" in texts[0]
            assert texts[1].startswith("Test pairs (") and texts[2].startswith("All pairs ("), texts

            await page.send("Page.navigate", url=f"{base}/#/unit/{first_example}?sample=example")
            await page.wait_until(f"{rows}.length === 2 && {rows}[0].classList.contains('example') && {rows}[1].classList.contains('all')")
            # all units in the file: only the whole file's bar
            await page.send("Page.navigate", url=f"{base}/#/unit/{first_example}?sample=all")
            await page.wait_until(f"{rows}.length === 1 && {rows}[0].classList.contains('all')")

    asyncio.run(run())


def test_select_like_this_pair_in_the_rail(gui):
    """
    In a compact unit, "same suggestion" and "same change" (select all pairs like the current one) are in the rail,
    not above the pairs; "same change" selects the pairs, and the selection bar appears.
    """
    base, ws_url = gui
    compact = "Mark.1.1.12-16"

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{compact}?sample=all")
            await page.wait_until("document.querySelectorAll('.pair.compact').length > 0")
            same = "document.querySelector('#pair-nav .select-same-change')"
            await page.wait_until(f"!!{same} && {same}.getClientRects().length > 0")
            assert await page.eval("!document.getElementById('compact-tools')")
            assert await page.eval("!!document.querySelector('#pair-nav .select-same-suggestion')")
            count = int((await page.eval(f"{same}.textContent")).split("(")[1].rstrip(")"))
            assert count >= 1
            await page.eval(f"{same}.click()")
            await page.wait_until("!document.getElementById('bulk-bar').hidden")
            assert await page.eval("document.querySelectorAll('#pairs .pair.ticked').length") == count

    asyncio.run(run())


def test_select_like_this_pair_not_shown_on_full_units(gui):
    """ Units shown as full panels have no selection tools (as before): the rail's section is hidden. """
    base, ws_url = gui

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{REASON_UNIT}?sample=all")
            await page.wait_until("document.querySelectorAll('#pairs .pair.full').length > 0 && !!document.querySelector('#pair-nav .unit-nav')")
            assert await page.eval("!document.querySelector('#pair-nav .rail-select') || document.querySelector('#pair-nav .rail-select').hidden")

    asyncio.run(run())


@pytest.mark.parametrize("prepare", [draw_a_sample])
def test_no_suggestions_section_on_test_units(gui):
    """ Test units never show suggestions, so the rail has no Suggestions section there; example units keep it. """
    base, ws_url = gui
    test_unit = wait_for(f"{base}/api/queue?sample=test")["units"][0]["id"]
    example_unit = wait_for(f"{base}/api/queue?sample=example")["units"][0]["id"]
    section = "document.querySelector('#pair-nav .rail-suggestions')"

    async def run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            page = Page(ws)
            await page.send("Page.navigate", url=f"{base}/#/unit/{test_unit}?sample=sampled")
            await page.wait_until(f"{HEADER_UNIT} === '{test_unit}' && !!{section}")
            assert await page.eval(f"{section}.getClientRects().length") == 0
            await page.send("Page.navigate", url=f"{base}/#/unit/{example_unit}?sample=sampled")
            await page.wait_until(f"{HEADER_UNIT} === '{example_unit}'")
            await page.wait_until(f"{section}.getClientRects().length > 0")

    asyncio.run(run())


def test_wait_for_chrome_reads_the_devtools_port(tmp_path):
    port_file, log = tmp_path / "DevToolsActivePort", tmp_path / "chrome.log"
    port_file.write_text("54321\n/devtools/browser/abc\n")
    log.write_text("")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert wait_for_chrome(process, port_file, log, seconds=5) == 54321
    finally:
        process.kill()
        process.wait()


def test_wait_for_chrome_reports_an_early_exit_with_its_stderr(tmp_path):
    log = tmp_path / "chrome.log"
    with open(log, "wb") as stderr:
        process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stderr.write('no display'); sys.exit(3)"], stderr=stderr)
    process.wait()
    with pytest.raises(AssertionError, match="(?s)exited with code 3.*no display"):
        wait_for_chrome(process, tmp_path / "DevToolsActivePort", log, seconds=5)

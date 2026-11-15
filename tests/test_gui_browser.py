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
def gui(tmp_path):
    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    port = free_port()
    server = subprocess.Popen([RDGAI, "annotate", str(path), "--port", str(port), "--annotator", "RT"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    debug_port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", f"--remote-debugging-port={debug_port}",
                               f"--user-data-dir={tmp_path / 'chrome'}", "--window-size=1400,1000", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(f"http://127.0.0.1:{port}/api/config")
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
            await page.eval("[...document.querySelectorAll('#header-unit button')].find((b) => b.textContent.startsWith('Next →')).click()")
            await page.wait_until(f"document.querySelector('#header-unit h1')?.textContent === '{simple}'")
            await asyncio.sleep(0.5)
            shown = await page.eval(VISIBLE_TOP_CARDS)
            # no card from the previous unit may be visible; any card shown belongs to this unit
            assert set(shown) <= set(unit_readings(base, simple)), shown
            # the cards (where shown) are marked with the unit they belong to, matching the header
            marked = await page.eval("document.getElementById('readings').dataset.unit || null")
            assert marked in (None, simple)

            # and back: the compact unit's cards are shown again, all of them
            await page.eval("[...document.querySelectorAll('#header-unit button')].find((b) => b.textContent.startsWith('← Prev')).click()")
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

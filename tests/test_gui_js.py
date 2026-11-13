"""
Runs the JavaScript tests for the GUI's pure logic (tests/js) with Node's built-in test runner.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
JS_TESTS = Path(__file__).parent / "js"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gui_logic_js():
    files = sorted(str(path) for path in JS_TESTS.glob("*.test.mjs"))
    assert files
    result = subprocess.run([NODE, "--test", *files], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

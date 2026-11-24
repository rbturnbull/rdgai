"""
The GUI runs inside the MCP server process (`rdgai serve-mcp --gui-port`), sharing one store, so only one
program writes the TEI file. If the port is taken, the MCP server still runs.
"""
import shutil
import socket
import sys
from pathlib import Path

import anyio
import httpx
import pytest

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from .util import TEST_DATA_DIR

RDGAI = str(Path(sys.executable).with_name("rdgai"))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_for_gui(port:int) -> httpx.Response:
    async with httpx.AsyncClient() as http:
        for _ in range(100):
            try:
                return await http.get(f"http://127.0.0.1:{port}/api/config")
            except httpx.ConnectError:
                await anyio.sleep(0.1)
    raise AssertionError("GUI did not start")


@pytest.mark.skipif(not Path(RDGAI).exists(), reason="rdgai entry point not installed")
def test_gui_inside_mcp_process(tmp_path):
    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    port = free_port()

    async def run():
        params = StdioServerParameters(command=RDGAI, args=["serve-mcp", str(path), "--annotator", "RT", "--gui-port", str(port)])
        async with Client(params) as client:
            config = await wait_for_gui(port)
            assert config.json()["annotator"] == "RT"

            async with httpx.AsyncClient() as http:
                response = await http.post(f"http://127.0.0.1:{port}/api/label", json={
                    "unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": "Major_Omission",
                })
                assert response.status_code == 200

                unit = (await client.call_tool("get_unit", {"unit_id": "Mark.1.1.10"})).structured_content
                assert unit["pairs"][0]["category"] == "Major_Omission"

                await client.call_tool("set_classification", {"unit_id": "Mark.1.2.2", "active": "a", "passive": "b", "category": "Minor_Substitution"})
                pair = (await http.get(f"http://127.0.0.1:{port}/api/unit/Mark.1.2.2")).json()["pairs"][0]
                assert pair["category"] == "Minor_Substitution"

    anyio.run(run)


@pytest.mark.skipif(not Path(RDGAI).exists(), reason="rdgai entry point not installed")
def test_mcp_still_runs_if_gui_port_is_taken(tmp_path, monkeypatch):
    import mcp.client.client as client_module

    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    errors_path = tmp_path / "stderr.txt"
    errors = open(errors_path, "w")
    original = client_module.stdio_client
    monkeypatch.setattr(client_module, "stdio_client", lambda server: original(server, errlog=errors))

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]

        async def run():
            params = StdioServerParameters(command=RDGAI, args=["serve-mcp", str(path), "--annotator", "RT", "--gui-port", str(port)])
            async with Client(params) as client:
                tools = await client.list_tools()
                assert "get_unit" in [tool.name for tool in tools.tools]
                await anyio.sleep(0.5)

        anyio.run(run)
    errors.close()
    assert f"GUI not started: port {port}" in errors_path.read_text()


@pytest.mark.skipif(not Path(RDGAI).exists(), reason="rdgai entry point not installed")
def test_gui_port_is_taken_over_when_freed(tmp_path, monkeypatch):
    """ Claude Desktop starts two serve-mcp processes: if the one with the GUI exits, the other takes the port over. """
    import mcp.client.client as client_module

    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    errors_path = tmp_path / "stderr.txt"
    errors = open(errors_path, "w")
    original = client_module.stdio_client
    monkeypatch.setattr(client_module, "stdio_client", lambda server: original(server, errlog=errors))

    busy = socket.socket()
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]

    async def run():
        params = StdioServerParameters(command=RDGAI, args=[
            "serve-mcp", str(path), "--annotator", "RT", "--gui-port", str(port), "--gui-retry-seconds", "0.3",
        ])
        async with Client(params) as client:
            assert "get_unit" in [tool.name for tool in (await client.list_tools()).tools]
            await anyio.sleep(0.5)
            busy.close()  # the process that had the GUI exits
            config = await wait_for_gui(port)
            assert config.json()["annotator"] == "RT"
            # MCP is unaffected
            assert not (await client.call_tool("get_unit", {"unit_id": "Mark.1.1.10"})).is_error

    anyio.run(run)
    errors.close()
    log = errors_path.read_text()
    assert f"GUI not started: port {port}" in log
    assert "will try the port again every 0.3 s" in log
    assert f"took over the GUI port: annotation GUI at http://127.0.0.1:{port}/" in log

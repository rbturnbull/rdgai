"""
Running the GUI on its own, or in the same process (and event loop) as the MCP server, sharing its store.
"""
import socket
import sys

import anyio
import uvicorn


def bind(host:str, port:int) -> socket.socket | None:
    """ Binds a listening socket, or returns None (with a message on stderr) if the port is unavailable. """
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except OSError as error:
        sock.close()
        print(f"rdgai: GUI not started: port {port} on {host} is unavailable ({error.strerror}).", file=sys.stderr, flush=True)
        return None


def gui_server(app, host:str, port:int) -> uvicorn.Server:
    # No access log and no logging configuration: uvicorn's access log writes to stdout, which is the MCP stdio channel.
    config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False, lifespan="off")
    return uvicorn.Server(config)


async def serve_mcp_with_gui(mcp_server, gui_app, gui_host:str, gui_port:int, transport:str="stdio", host:str="127.0.0.1", port:int=8000):
    """ Runs the MCP server and, if its port is free, the GUI in the same event loop. The MCP server always runs. """

    async def run_mcp():
        if transport == "stdio":
            await mcp_server.run_stdio_async()
        else:
            await mcp_server.run_streamable_http_async(host=host, port=port)

    sock = bind(gui_host, gui_port)
    if sock is None:
        await run_mcp()
        return

    gui = gui_server(gui_app, gui_host, gui_port)
    print(f"rdgai: annotation GUI at http://{gui_host}:{gui_port}/", file=sys.stderr, flush=True)
    async with anyio.create_task_group() as group:
        group.start_soon(gui.serve, [sock])
        try:
            await run_mcp()
        finally:
            gui.should_exit = True

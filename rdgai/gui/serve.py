"""
Running the GUI on its own, or in the same process (and event loop) as the MCP server, sharing its store.
"""
import socket
import sys

import anyio
import uvicorn


def bind(host:str, port:int, quiet:bool=False, retry_seconds:float=0) -> socket.socket | None:
    """ Binds a listening socket, or returns None (with a message on stderr unless quiet) if the port is unavailable. """
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except OSError as error:
        sock.close()
        if not quiet:
            retry = f" MCP is still running; will try the port again every {retry_seconds:g} s." if retry_seconds > 0 else " MCP is still running."
            print(f"rdgai: GUI not started: port {port} on {host} is unavailable ({error.strerror}).{retry}", file=sys.stderr, flush=True)
        return None


def gui_server(app, host:str, port:int) -> uvicorn.Server:
    # No access log and no logging configuration: uvicorn's access log writes to stdout, which is the MCP stdio channel.
    config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False, lifespan="off")
    return uvicorn.Server(config)


async def serve_mcp_with_gui(
    mcp_server, gui_app, gui_host:str, gui_port:int, transport:str="stdio", host:str="127.0.0.1", port:int=8000,
    retry_seconds:float=15.0,
):
    """
    Runs the MCP server and, if its port is free, the GUI in the same event loop. The MCP server always runs.
    If the GUI port is taken (e.g. by a second process for the same file), it is tried again every retry_seconds
    (0: never), so that the GUI survives whichever process exits first.
    """

    async def run_mcp():
        if transport == "stdio":
            await mcp_server.run_stdio_async()
        else:
            await mcp_server.run_streamable_http_async(host=host, port=port)

    gui = gui_server(gui_app, gui_host, gui_port)
    url = f"http://{gui_host}:{gui_port}/"

    async def take_over_when_free():
        while True:
            await anyio.sleep(retry_seconds)
            sock = bind(gui_host, gui_port, quiet=True)
            if sock is not None:
                print(f"rdgai: took over the GUI port: annotation GUI at {url}", file=sys.stderr, flush=True)
                await gui.serve([sock])
                return

    sock = bind(gui_host, gui_port, retry_seconds=retry_seconds)
    async with anyio.create_task_group() as group:
        if sock is not None:
            print(f"rdgai: annotation GUI at {url}", file=sys.stderr, flush=True)
            group.start_soon(gui.serve, [sock])
        elif retry_seconds > 0:
            group.start_soon(take_over_when_free)
        try:
            await run_mcp()
        finally:
            gui.should_exit = True
            group.cancel_scope.cancel()

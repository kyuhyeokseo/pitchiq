"""PitchIQ as a Model Context Protocol server: the read-only match tools (``tools.py``) for any MCP client, such as a
Microsoft Foundry agent, GitHub Copilot or a desktop app.

    PYTHONPATH=backend python -m app.agents.mcp_server            # stdio
    PYTHONPATH=backend python -m app.agents.mcp_server --http 8765  # streamable HTTP at http://127.0.0.1:8765/mcp

The web app also serves the tools at ``/mcp`` (``HttpEndpoint``), so a deployment needs no second process.

Every tool is read-only and causal: it answers from what the analysis knew at the asked moment. Needs the optional
``mcp`` extra.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import importlib.util
import os
from collections.abc import AsyncIterator, Callable

from . import tools

INSTRUCTIONS = """PitchIQ analyses synthetic football tracking clips for defensive trade-offs: what a defending
structure restricts (for example screened short passing options) and what it exposes (for example space behind the
line). Start with list_clips, then defensive_moments for a clip, then moment_evidence or explain_moment for one
moment. Every number comes from the analysis; quote values as returned and do not compute new ones. Claims marked
'heuristic' are estimates under stated assumptions, and outcomes are observations of synthetic play, not intent."""

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
PUBLIC_HOSTS_ENV = "PITCHIQ_PUBLIC_HOSTS"


def available() -> bool:
    """Whether the optional ``mcp`` extra is installed."""
    return importlib.util.find_spec("mcp") is not None


def build_server():
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    srv = MCPServer(name="pitchiq", title="PitchIQ defensive intelligence", instructions=INSTRUCTIONS,
                    version="0.1.0")
    ro = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
    for fn in tools.TOOLS:
        srv.tool(annotations=ro)(_client_errors(fn))
    return srv


def _client_errors(fn):
    """The tool with its own signature, whose ToolError reaches the client as text ("unknown clip ...; call
    list_clips") instead of the SDK's generic "Error executing tool"."""
    from mcp.server.mcpserver.exceptions import ToolError as McpToolError

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except tools.ToolError as e:
            raise McpToolError(str(e)) from None
    return wrapped


def public_hosts(value: str) -> list[str]:
    """Host names from a comma-separated list such as ``PITCHIQ_PUBLIC_HOSTS``; a scheme, a path and a port are
    dropped, so ``https://pitchiq.example.org:443/x`` means ``pitchiq.example.org`` (any port is allowed for it)."""
    out = []
    for h in value.split(","):
        h = h.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
        if not h.startswith("["):  # keep IPv6 literals whole
            h = h.split(":")[0]
        if h and h not in out:
            out.append(h)
    return out


def transport_security(hosts: list[str]):
    """DNS-rebinding protection on: the Host header must be loopback or one of ``hosts`` (with or without a port),
    and a browser's Origin, when sent, must be http on loopback or https on one of ``hosts``."""
    from mcp.server.transport_security import TransportSecuritySettings

    allowed_hosts = [x for h in (*LOCAL_HOSTS, *hosts) for x in (h, f"{h}:*")]
    origins = ([x for h in LOCAL_HOSTS for x in (f"http://{h}", f"http://{h}:*")]
               + [x for h in hosts for x in (f"https://{h}", f"https://{h}:*")])
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts,
                                     allowed_origins=origins)


class HttpEndpoint:
    """The tools as a streamable HTTP endpoint inside another ASGI app (the web app routes ``/mcp`` here).

    Stateless with JSON responses: each POST is answered on its own, so it needs no session affinity and holds no
    open stream; GET (a server-to-client stream) and DELETE (ending a session) answer 405. A session manager can run
    only once, so each lifespan of the host app (``run``) starts a fresh one and reads the allowed hosts then."""

    def __init__(self, hosts: Callable[[], str] = lambda: os.environ.get(PUBLIC_HOSTS_ENV, "")):
        self._server = build_server()
        self._hosts = hosts
        self._manager = None

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        self._server.streamable_http_app(stateless_http=True, json_response=True,
                                         transport_security=transport_security(public_hosts(self._hosts())))
        manager = self._server.session_manager
        async with manager.run():
            previous, self._manager = self._manager, manager
            try:
                yield
            finally:
                self._manager = previous  # an inner lifespan (tests) hands the endpoint back to the outer one

    async def __call__(self, scope, receive, send) -> None:
        from starlette.responses import PlainTextResponse

        if self._manager is None:
            await PlainTextResponse("MCP endpoint is not running", status_code=503)(scope, receive, send)
        elif scope["method"] != "POST":
            await PlainTextResponse("MCP clients POST JSON-RPC here", status_code=405,
                                    headers={"Allow": "POST"})(scope, receive, send)
        else:
            await self._manager.handle_request(scope, receive, send)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=0, help="serve streamable HTTP on this port instead of stdio")
    args = ap.parse_args(argv)
    srv = build_server()
    if args.http:
        srv.run("streamable-http", port=args.http)
    else:
        srv.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

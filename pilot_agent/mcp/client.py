"""
MCP clients — pilot + Context7.

Architecture :
  PilotMCPClient   — connecte `pilot mcp serve` (stdio)
  Context7MCPClient — connecte `npx @upstash/context7-mcp` (stdio)
  MCPHub           — agrège N clients, route les appels au bon serveur

Usage :
    async with MCPHub.connect() as hub:
        tools = hub.tool_schemas()
        result = await hub.call("pilot_status", {})
        result = await hub.call("get-library-docs", {"libraryId": "/docker/docker", ...})
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

# ── Outils dangereux — nécessitent une approbation humaine ──────────────────

DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    "pilot_deploy",
    "pilot_rollback",
    "pilot_down",
    "pilot_secrets_inject",
    "pilot_push",
})

# ── Virtual tools — gérés par le REPL, pas par un serveur MCP ────────────────
# Ces outils sont injectés dans le schema LLM mais routés vers des callbacks UI.

VIRTUAL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "collect_credential",
            "description": (
                "Ask the user to enter a credential or secret directly in the terminal, "
                "then store it in the pilot process environment.\n\n"
                "ALWAYS use this instead of asking the user to run 'export' commands. "
                "The credential is immediately available to pilot_push, pilot_deploy, etc.\n\n"
                "Use secret=true for passwords and tokens (input will be masked).\n"
                "Use secret=false for usernames and non-sensitive values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Environment variable name (e.g. DOCKER_USERNAME, DOCKER_PASSWORD, GITHUB_TOKEN)",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Human-readable label shown to the user (e.g. 'Docker Hub username')",
                    },
                    "secret": {
                        "type": "boolean",
                        "description": "If true, input is masked. Default true for passwords/tokens.",
                    },
                },
                "required": ["key", "prompt"],
            },
        },
    }
]


# ── Client MCP générique ─────────────────────────────────────────────────────

class _MCPClient:
    """Client MCP de base — wraps une session MCP active."""

    def __init__(self, session: ClientSession, tools_list: list[dict], name: str = ""):
        self._session = session
        self._tools = tools_list
        self.name = name

    def tool_schemas(self) -> list[dict]:
        return self._tools

    def owns(self, tool_name: str) -> bool:
        """Vérifie si ce client possède l'outil demandé."""
        return any(
            t["function"]["name"] == tool_name for t in self._tools
        )

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._session.call_tool(name, arguments)
        if result.isError:
            parts = [c.text for c in result.content if hasattr(c, "text")]
            raise RuntimeError(f"Tool {name!r} failed: {' '.join(parts)}")
        parts = []
        for c in result.content:
            if hasattr(c, "text"):
                parts.append(c.text)
            elif hasattr(c, "data"):
                parts.append(json.dumps(c.data, ensure_ascii=False))
        return "\n".join(parts) or "(empty response)"


# ── MCPHub — agrégateur multi-serveurs ────────────────────────────────────────

class MCPHub:
    """
    Agrège plusieurs clients MCP en une interface unifiée.

    Le LLM voit tous les outils ; les appels sont routés au bon serveur.
    """

    def __init__(self, clients: list[_MCPClient]) -> None:
        self._clients = clients

    def tool_schemas(self) -> list[dict]:
        """Tous les outils de tous les serveurs + virtual tools, fusionnés."""
        schemas: list[dict] = []
        for client in self._clients:
            schemas.extend(client.tool_schemas())
        schemas.extend(VIRTUAL_TOOLS)
        return schemas

    def is_destructive(self, tool_name: str) -> bool:
        return tool_name in DESTRUCTIVE_TOOLS

    def is_virtual(self, tool_name: str) -> bool:
        """True si l'outil est un virtual tool géré par le REPL, pas par MCP."""
        return any(
            vt["function"]["name"] == tool_name for vt in VIRTUAL_TOOLS
        )

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Route l'appel au client qui possède l'outil."""
        for client in self._clients:
            if client.owns(name):
                return await client.call(name, arguments)
        raise RuntimeError(f"Outil {name!r} introuvable dans les serveurs MCP connectés.")

    @staticmethod
    @asynccontextmanager
    async def connect(
        pilot_cmd: str = "pilot",
        with_context7: bool = True,
    ) -> AsyncGenerator["MCPHub", None]:
        """
        Lance pilot + optionnellement Context7, retourne un MCPHub unifié.

        Context7 est optionnel : si npx est absent ou le serveur échoue,
        l'agent continue avec les seuls outils pilot.
        """
        async with PilotMCPClient.connect(pilot_cmd) as pilot_client:
            clients: list[_MCPClient] = [pilot_client._inner]

            if with_context7:
                try:
                    async with Context7MCPClient.connect() as c7_client:
                        clients.append(c7_client._inner)
                        yield MCPHub(clients)
                        return
                except Exception as e:
                    logger.debug("Context7 indisponible (npx manquant ?) : %s", e)
                    # Continue sans Context7

            yield MCPHub(clients)


# ── PilotMCPClient ────────────────────────────────────────────────────────────

class PilotMCPClient:
    """
    Client pour `pilot mcp serve`.

    Conservé pour compatibilité avec les imports existants.
    Prefer MCPHub.connect() pour le nouveau code.
    """

    def __init__(self, inner: _MCPClient) -> None:
        self._inner = inner

    def tool_schemas(self) -> list[dict]:
        return self._inner.tool_schemas()

    def is_destructive(self, tool_name: str) -> bool:
        return tool_name in DESTRUCTIVE_TOOLS

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return await self._inner.call(name, arguments)

    @staticmethod
    @asynccontextmanager
    async def connect(
        pilot_cmd: str = "pilot",
        pilot_args: list[str] | None = None,
        cwd: str | None = None,
    ) -> AsyncGenerator["PilotMCPClient", None]:
        args = pilot_args or ["mcp", "serve"]
        params = StdioServerParameters(command=pilot_cmd, args=args, env=None)

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resp = await session.list_tools()
                schemas = _to_openai_tools(resp.tools)
                yield PilotMCPClient(_MCPClient(session, schemas, name="pilot"))


# ── Context7MCPClient ─────────────────────────────────────────────────────────

class Context7MCPClient:
    """
    Client pour le serveur MCP Context7 (@upstash/context7-mcp).

    Fournit deux outils au LLM :
      - resolve-library-id  : trouve l'ID Context7 d'une lib (ex: "docker")
      - get-library-docs    : récupère la doc à jour d'une lib

    Prérequis : Node.js + npx installés.
    """

    def __init__(self, inner: _MCPClient) -> None:
        self._inner = inner

    def tool_schemas(self) -> list[dict]:
        return self._inner.tool_schemas()

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return await self._inner.call(name, arguments)

    @staticmethod
    @asynccontextmanager
    async def connect() -> AsyncGenerator["Context7MCPClient", None]:
        params = StdioServerParameters(
            command="npx",
            args=["-y", "@upstash/context7-mcp@latest"],
            env=None,
        )

        async with stdio_client(params, errlog=open(os.devnull, "w")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resp = await session.list_tools()
                schemas = _to_openai_tools(resp.tools)
                yield Context7MCPClient(_MCPClient(session, schemas, name="context7"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_openai_tools(tools: list) -> list[dict]:
    """Convertit les outils MCP au format OpenAI function calling."""
    result = []
    for t in tools:
        schema = {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        }
        result.append(schema)
    return result

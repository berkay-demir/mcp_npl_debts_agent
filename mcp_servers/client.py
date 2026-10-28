import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from database import bank_tools, db_tools


class MCPToolClient:
    """Calls the local MCP server over the standard-I/O transport."""

    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]
        self.server_parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_servers.debt_mcp_server"],
            cwd=self.project_root,
            env={
                **os.environ,
                "DB_PATH": str(Path(db_tools.DB_PATH).resolve()),
                "BANK_DB_PATH": str(Path(bank_tools.BANK_DB_PATH).resolve()),
            },
        )

    def call_tool(self, name: str, **arguments: object) -> Any:
        try:
            return asyncio.run(self._call_tool(name, arguments))
        except Exception as exc:
            raise RuntimeError(f"MCP aracı '{name}' çağrılamadı: {exc}") from exc

    async def _call_tool(self, name: str, arguments: dict[str, object]) -> Any:
        async with stdio_client(self.server_parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                available_tools = await session.list_tools()
                if not any(tool.name == name for tool in available_tools.tools):
                    raise ValueError(f"MCP sunucusu '{name}' aracını sunmuyor.")
                result = await session.call_tool(name, arguments)

        response_text = "\n".join(
            item.text
            for item in result.content
            if hasattr(item, "text")
        )
        if result.is_error:
            raise RuntimeError(response_text or f"MCP aracı '{name}' hata döndürdü.")
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"MCP aracı '{name}' geçerli JSON yanıtı döndürmedi."
            ) from exc

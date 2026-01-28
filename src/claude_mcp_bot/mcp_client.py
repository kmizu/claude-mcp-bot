"""MCP Client - connects to multiple MCP servers and manages tools."""

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    """Client for connecting to multiple MCP servers."""

    def __init__(self):
        self.exit_stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.tool_to_server: dict[str, str] = {}

    async def connect(self, server_configs: dict[str, dict[str, Any]]) -> None:
        """Connect to multiple MCP servers.

        Args:
            server_configs: Dict of server name -> config with 'command' and 'args'
        """
        for server_name, config in server_configs.items():
            try:
                await self._connect_server(server_name, config)
                print(f"  Connected to {server_name}")
            except Exception as e:
                print(f"  Failed to connect to {server_name}: {e}")

    async def _connect_server(
        self, server_name: str, config: dict[str, Any]
    ) -> None:
        """Connect to a single MCP server."""
        server_params = StdioServerParameters(
            command=config["command"],
            args=config.get("args", []),
            env=config.get("env"),
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read_stream, write_stream = stdio_transport

        session = await self.exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        self.sessions[server_name] = session

        # Map tools to server
        tools_response = await session.list_tools()
        for tool in tools_response.tools:
            self.tool_to_server[tool.name] = server_name

    async def list_all_tools(self) -> list[dict[str, Any]]:
        """Get all tools from all connected servers in Claude API format."""
        all_tools = []

        for server_name, session in self.sessions.items():
            try:
                tools_response = await session.list_tools()
                for tool in tools_response.tools:
                    claude_tool = {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema,
                    }
                    all_tools.append(claude_tool)
            except Exception as e:
                print(f"Error listing tools from {server_name}: {e}")

        return all_tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Call a tool and return the result.

        Returns list of content blocks (text or image).
        """
        server_name = self.tool_to_server.get(tool_name)
        if not server_name:
            return [{"type": "text", "text": f"Unknown tool: {tool_name}"}]

        session = self.sessions.get(server_name)
        if not session:
            return [{"type": "text", "text": f"Server not connected: {server_name}"}]

        try:
            result = await session.call_tool(tool_name, arguments)

            contents = []
            for content in result.content:
                if content.type == "text":
                    contents.append({"type": "text", "text": content.text})
                elif content.type == "image":
                    contents.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": content.mimeType,
                            "data": content.data,
                        },
                    })
                else:
                    contents.append({"type": "text", "text": str(content)})

            return contents

        except Exception as e:
            return [{"type": "text", "text": f"Tool error: {e}"}]

    async def close(self) -> None:
        """Close all connections."""
        await self.exit_stack.aclose()

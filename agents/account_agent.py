from mcp_servers.client import MCPToolClient


class AccountAgent:
    """Retrieves verified bank balance and recent account movements."""

    def __init__(self, tool_client: MCPToolClient) -> None:
        self.tool_client = tool_client

    def get_account_activity(self, debtor_id: int, limit: int = 10) -> dict:
        return self.tool_client.call_tool(
            "get_account_activity",
            debtor_id=debtor_id,
            limit=limit,
        )

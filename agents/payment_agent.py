from mcp_servers.client import MCPToolClient


class PaymentAgent:
    """Validates and executes payments against a customer's bank account."""

    def __init__(self, tool_client: MCPToolClient) -> None:
        self.tool_client = tool_client

    def pay(
        self,
        debtor_id: int,
        payment_plan_id: int,
        payment_type: str,
        installment_number: int | None = None,
    ) -> dict:
        return self.tool_client.call_tool(
            "pay_payment_plan",
            debtor_id=debtor_id,
            payment_plan_id=payment_plan_id,
            payment_type=payment_type,
            installment_number=installment_number,
        )

import json

from mcp.server.mcpserver import MCPServer

from database.bank_tools import (
    get_account_activity,
    has_paid_down_payment,
    pay_payment_plan,
)
from database.db_tools import (
    get_customer_debts,
    get_debtor_info,
    get_payment_plan_history,
    get_policy_rules,
    save_payment_plan,
)


server = MCPServer(
    "npl-debt-tools",
    instructions="Borç, ödeme planı ve demo banka hesabı araçlarını sunar.",
)


@server.tool(
    name="get_debtor_info",
    description="Müşterinin TC kimlik numarasıyla borç ve finansal bilgilerini getirir.",
)
def get_debtor_info_tool(tc_no: str) -> str:
    return json.dumps(get_debtor_info(tc_no), ensure_ascii=False)


@server.tool(
    name="get_customer_debts",
    description="Müşterinin borç ürünlerini ve toplam borç tutarını getirir.",
)
def get_customer_debts_tool(debtor_id: int) -> str:
    return json.dumps(get_customer_debts(debtor_id), ensure_ascii=False)


@server.tool(
    name="get_payment_plan_history",
    description="Müşterinin kayıtlı ödeme planlarını ve taksit takvimini getirir.",
)
def get_payment_plan_history_tool(debtor_id: int) -> str:
    plans = get_payment_plan_history(debtor_id)
    for plan in plans:
        plan["down_payment_paid"] = has_paid_down_payment(plan["id"])
    return json.dumps(plans, ensure_ascii=False)


@server.tool(
    name="get_policy_rules",
    description="Borç tutarına göre geçerli yapılandırma kurallarını getirir.",
)
def get_policy_rules_tool(total_debt: float) -> str:
    return json.dumps(get_policy_rules(total_debt), ensure_ascii=False)


@server.tool(
    name="save_payment_plan",
    description="Müşterinin onayladığı ödeme planını ve taksit takvimini kaydeder.",
)
def save_payment_plan_tool(
    debtor_id: int,
    down_payment: float,
    installments: int,
    monthly: float,
    total: float,
) -> str:
    return json.dumps(
        save_payment_plan(debtor_id, down_payment, installments, monthly, total),
        ensure_ascii=False,
    )


@server.tool(
    name="get_account_activity",
    description="Demo banka bakiyesini ve son hesap hareketlerini getirir.",
)
def get_account_activity_tool(debtor_id: int, limit: int = 10) -> str:
    return json.dumps(
        get_account_activity(debtor_id, limit=limit),
        ensure_ascii=False,
    )


@server.tool(
    name="pay_payment_plan",
    description="Yeterli bakiye ve ödeme kuralları uygunsa peşinat veya taksit öder.",
)
def pay_payment_plan_tool(
    debtor_id: int,
    payment_plan_id: int,
    payment_type: str,
    installment_number: int | None = None,
) -> str:
    return json.dumps(
        pay_payment_plan(
            debtor_id=debtor_id,
            payment_plan_id=payment_plan_id,
            payment_type=payment_type,
            installment_number=installment_number,
        ),
        ensure_ascii=False,
    )


if __name__ == "__main__":
    server.run(transport="stdio")

from getpass import getpass

from database.db_setup import init_db
from database.db_tools import authenticate_customer, get_payment_plan_history
from agents.orchestrator import DebtCollectionOrchestrator
from tabulate import tabulate

def print_result_table(result):
    if result["status"] == "error":
        print(f"Hata: {result['message']}")
        return

    debtor = result["debtor"]
    print("\n========================================================")
    print(f" YASAL TAKİP ÖDEME YAPILANDIRMA RAPORU ")
    print("========================================================")
    print(f"Borçlu Adı Soyadı : {debtor['full_name']}")
    print(f"Toplam Borç Tutarı : {debtor['total_debt']:,.2f} TL")
    print(f"Takip Durumu : {debtor['debt_status']}")
    
    if result.get("custom_plan_status"):
        print(f"Müşteri Talebi Durumu: {result['custom_plan_status']}")

    if result.get("agent_summary"):
        print(f"\n🤖 [Ollama / Qwen Agent Değerlendirmesi]:\n{result['agent_summary']}")

    print("\n--- SUNULAN ÖDEME SEÇENEKLERİ ---")
    table_data = []
    for plan_number, plan in enumerate(result["plans"], start=1):
        table_data.append([
            plan_number,
            plan["option_name"],
            f"{plan['down_payment']:,.2f} TL",
            f"{plan['installments']} Ay",
            f"{plan['monthly_payment']:,.2f} TL",
            f"{plan['total_amount']:,.2f} TL",
            f"{plan['savings']:,.2f} TL"
        ])

    headers = ["No", "Plan Seçeneği", "Peşinat", "Vade", "Aylık Taksit", "Toplam Ödeme", "Kazanç / İndirim"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))


def print_saved_plans(debtor_id):
    history = get_payment_plan_history(debtor_id)
    if not history:
        return

    print("\n--- KAYITLI ÖDEME PLANI ---")
    for plan in history:
        print(
            f"Plan #{plan['id']} | Peşinat: {plan['down_payment']:,.2f} TL | "
            f"{plan['installment_count']} taksit | Toplam: {plan['total_agreed_amount']:,.2f} TL"
        )
        for installment in plan["installments"]:
            print(
                f"  {installment['installment_number']}. taksit: "
                f"{installment['amount']:,.2f} TL - {installment['due_date']} ({installment['status']})"
            )

def print_debt_accounts(result):
    if result["status"] == "error":
        print(f"Hata: {result['message']}")
        return

    rows = []
    for account in result["debt_accounts"]:
        rows.append([
            account["product_name"],
            f"{account['principal_debt']:,.2f} TL",
            f"{account['accrued_interest']:,.2f} TL",
            f"{account['legal_costs']:,.2f} TL",
            f"{account['principal_debt'] + account['accrued_interest'] + account['legal_costs']:,.2f} TL",
            account["status"],
        ])

    print("\n--- BANKA BORÇ / KREDİ HESAPLARIM ---")
    print(tabulate(
        rows,
        headers=["Ürün / Kredi", "Anapara", "İşleyen Faiz", "Masraf", "Toplam Borç", "Durum"],
        tablefmt="grid",
    ))
    print(
        f"Genel Toplam: {result['total_debt']:,.2f} TL "
        f"(Anapara: {result['principal_debt']:,.2f} TL, "
        f"Faiz: {result['accrued_interest']:,.2f} TL, "
        f"Masraf: {result['legal_costs']:,.2f} TL)"
    )


def customer_session(orchestrator, account):
    print(f"\nHoş geldiniz, {account['full_name']}.")
    if not orchestrator.llm:
        print(
            "Sohbet için Ollama LLM gerekli. langchain-ollama paketinin kurulu "
            "olduğunu, Ollama servisinin çalıştığını ve modelin indirildiğini kontrol edin."
        )
        return

    state = {}
    while True:
        message = input("\nSiz: ").strip()
        if not message:
            continue

        result = orchestrator.handle_customer_message(message, account, state)
        print(f"\nAsistan: {result['reply']}")
        if result.get("debt_result"):
            print_debt_accounts(result["debt_result"])
        if result.get("plan_result", {}).get("status") == "success":
            print_result_table(result["plan_result"])
        if result.get("saved_plans"):
            print_saved_plans(account["debtor_id"])
        if result.get("saved_plan"):
            print_saved_plans(account["debtor_id"])
        if result["status"] == "exit":
            return


def run_console():
    init_db()
    orchestrator = DebtCollectionOrchestrator()

    while True:
        customer_no = input("\nMüşteri numarası (çıkış için X): ").strip()
        if customer_no.upper() == "X":
            print("Görüşmek üzere.")
            break
        password = getpass("Şifre: ")
        account = authenticate_customer(customer_no, password)
        if account["status"] == "error":
            print(account["message"])
            continue
        customer_session(orchestrator, account)


if __name__ == "__main__":
    run_console()

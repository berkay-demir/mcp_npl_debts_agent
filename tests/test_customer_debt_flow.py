import contextlib
import json
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

from database import bank_tools, db_setup, db_tools
from agents.orchestrator import DebtCollectionOrchestrator, OLLAMA_BASE_URL, OLLAMA_MODEL
from main import customer_session, run_console
from mcp_servers.client import MCPToolClient


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=next(self.outputs))


class CustomerDebtFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "test_debts.db")
        self.bank_db_path = os.path.join(self.temp_dir.name, "test_bank.db")
        for module in (db_setup, db_tools):
            db_path_patch = patch.object(module, "DB_PATH", self.db_path)
            db_path_patch.start()
            self.addCleanup(db_path_patch.stop)
        bank_db_path_patch = patch.object(bank_tools, "DB_PATH", self.db_path)
        bank_db_path_patch.start()
        self.addCleanup(bank_db_path_patch.stop)
        bank_path_patch = patch.object(bank_tools, "BANK_DB_PATH", self.bank_db_path)
        bank_path_patch.start()
        self.addCleanup(bank_path_patch.stop)
        db_setup.init_db()

    def test_chat_model_disables_reasoning_for_json_responses(self):
        with patch("agents.orchestrator.OLLAMA_AVAILABLE", True), patch(
            "agents.orchestrator.ChatOllama"
        ) as chat_ollama:
            DebtCollectionOrchestrator()

        chat_ollama.assert_called_once_with(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            temperature=0.1,
            format="json",
            reasoning=False,
        )

    def test_demo_product_breakdown_matches_restructuring_debt(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        debt_result = db_tools.get_customer_debts(account["debtor_id"])
        debtor_result = db_tools.get_debtor_info(account["tc_no"])

        self.assertEqual(debt_result["status"], "success")
        self.assertEqual(len(debt_result["debt_accounts"]), 3)
        self.assertEqual(debt_result["total_debt"], 19500.0)
        self.assertEqual(debtor_result["status"], "success")
        self.assertEqual(debtor_result["debt_status"], "ACTIVE")
        self.assertEqual(debtor_result["total_debt"], debt_result["total_debt"])

    def test_local_mcp_client_calls_debt_tool(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")

        result = MCPToolClient().call_tool(
            "get_customer_debts",
            debtor_id=account["debtor_id"],
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_debt"], 19500.0)

    def test_existing_unclassified_debts_are_migrated_without_changing_totals(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute('''
                INSERT INTO debtors
                (tc_no, full_name, principal_debt, accrued_interest, legal_costs)
                VALUES (?, ?, ?, ?, ?)
            ''', ("44444444444", "Test Müşteri", 1200.0, 300.0, 50.0))
            conn.commit()

        db_setup.init_db()
        with closing(sqlite3.connect(self.db_path)) as conn:
            debtor_id = conn.execute(
                "SELECT id FROM debtors WHERE tc_no = ?", ("44444444444",)
            ).fetchone()[0]
        result = db_tools.get_customer_debts(debtor_id)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["debt_accounts"][0]["product_name"], "Diğer Borç")
        self.assertEqual(result["total_debt"], 1550.0)

    def test_chat_routes_natural_language_debt_inquiry_to_tool(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "debt_inquiry",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
            }),
            json.dumps({"reply": "Borç hesaplarınızı aşağıda listeledim."}),
        ])
        state = {}

        response = orchestrator.handle_customer_message(
            "Borç bilgilerimi görüntüle", account, state
        )

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["debt_result"]["total_debt"], 19500.0)
        self.assertIn("Borç hesaplarınızı", response["reply"])

    def test_classifier_accepts_literal_null_payment_type_for_nonpayment_intent(self):
        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "create_plan",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
                "payment_type": "null",
                "installment_number": None,
            }),
        ])

        action = orchestrator._classify_customer_message(
            "Ödeme planları tekliflerini getir", {}
        )

        self.assertEqual(action["intent"], "create_plan")
        self.assertIsNone(action["payment_type"])

    def test_chat_can_create_select_and_confirm_payment_plan(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "create_plan",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
            }),
            json.dumps({"summary": "Ödeme planı teklifleri hazır."}),
            json.dumps({"reply": "Teklifleri hazırladım; hangisini tercih edersiniz?"}),
            json.dumps({
                "intent": "select_plan",
                "installments": None,
                "down_payment": None,
                "plan_number": 1,
            }),
            json.dumps({"reply": "Peşin kapatma teklifini onaylıyor musunuz?"}),
            json.dumps({
                "intent": "confirm_plan",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
            }),
            json.dumps({"reply": "Planınız kaydedildi."}),
        ])
        state = {}

        offer_response = orchestrator.handle_customer_message(
            "Ödeme planı oluştur", account, state
        )
        select_response = orchestrator.handle_customer_message(
            "Peşin kapatma teklifini seçiyorum", account, state
        )
        save_response = orchestrator.handle_customer_message(
            "Onaylıyorum", account, state
        )

        history = db_tools.get_payment_plan_history(account["debtor_id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["installment_count"], 0)
        self.assertEqual(offer_response["status"], "success")
        self.assertEqual(select_response["status"], "success")
        self.assertTrue(save_response["saved_plan"])
        self.assertEqual(state["stage"], "idle")
        debt_result = db_tools.get_customer_debts(account["debtor_id"])
        self.assertTrue(all(item["status"] == "AGREED" for item in debt_result["debt_accounts"]))

    def test_customer_can_accept_offer_by_installment_count(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "accept_plan",
                "installments": 3,
                "down_payment": None,
                "plan_number": None,
                "payment_type": None,
                "installment_number": None,
            }),
            json.dumps({"reply": "3 taksitli teklif kaydedildi."}),
        ])
        state = {
            "stage": "choosing_plan",
            "plans": [
                {
                    "option_name": "A) Peşin Kapatma",
                    "down_payment": 18000,
                    "installments": 0,
                    "monthly_payment": 0,
                    "total_amount": 18000,
                    "savings": 1500,
                },
                {
                    "option_name": "B) Standart Plan (6 Taksit)",
                    "down_payment": 3900,
                    "installments": 6,
                    "monthly_payment": 2600,
                    "total_amount": 19500,
                    "savings": 0,
                },
                {
                    "option_name": "C) Müşteri Özel Talebi (3 Taksit)",
                    "down_payment": 3900,
                    "installments": 3,
                    "monthly_payment": 4900,
                    "total_amount": 18600,
                    "savings": 900,
                },
            ],
            "selected_plan": None,
            "history": [{"role": "assistant", "content": "Ödeme planı teklifleri hazır."}],
        }

        response = orchestrator.handle_customer_message(
            "3 taksitli ödeme planı teklifini kabul ediyorum", account, state
        )

        history = db_tools.get_payment_plan_history(account["debtor_id"])
        self.assertEqual(response["status"], "success")
        self.assertTrue(response["saved_plan"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["installment_count"], 3)
        self.assertEqual(history[0]["total_agreed_amount"], 18600)
        self.assertEqual(state["stage"], "idle")

    def test_chat_reports_previously_approved_cash_plan_without_recreating_it(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        saved = db_tools.save_payment_plan(
            debtor_id=account["debtor_id"],
            down_payment=18000,
            installments=0,
            monthly=0,
            total=18000,
        )
        self.assertEqual(saved["status"], "success")

        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "confirm_plan",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
            }),
            json.dumps({
                "intent": "plan_inquiry",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
            }),
        ])
        state = {}

        confirmation_response = orchestrator.handle_customer_message(
            "Peşin kapatmayı yapacağım", account, state
        )
        inquiry_response = orchestrator.handle_customer_message(
            "Aktif bir ödeme planım mı var", account, state
        )

        history = db_tools.get_payment_plan_history(account["debtor_id"])
        self.assertEqual(len(history), 1)
        for response in (confirmation_response, inquiry_response):
            self.assertIn("kayıtlı bir peşin kapatma ödeme planı var", response["reply"])
            self.assertIn("Peşinat durumu: Ödenmedi", response["reply"])
        self.assertTrue(confirmation_response["saved_plans"])
        self.assertTrue(inquiry_response["saved_plans"])

    def test_down_payment_uses_bank_balance_and_cannot_be_charged_twice(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        plan = db_tools.save_payment_plan(
            debtor_id=account["debtor_id"],
            down_payment=18000,
            installments=0,
            monthly=0,
            total=18000,
        )
        self.assertEqual(plan["status"], "success")
        payment_plan_id = db_tools.get_payment_plan_history(account["debtor_id"])[0]["id"]

        result = bank_tools.pay_payment_plan(
            account["debtor_id"], payment_plan_id, "down_payment"
        )
        repeated_result = bank_tools.pay_payment_plan(
            account["debtor_id"], payment_plan_id, "down_payment"
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["balance"], 2000)
        self.assertTrue(result["plan_closed"])
        self.assertEqual(repeated_result["status"], "error")
        self.assertEqual(bank_tools.get_bank_balance(account["debtor_id"]), 2000)
        debt_result = db_tools.get_customer_debts(account["debtor_id"])
        self.assertTrue(all(item["status"] == "CLOSED" for item in debt_result["debt_accounts"]))

    def test_chat_payment_request_uses_payment_agent(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        db_tools.save_payment_plan(
            debtor_id=account["debtor_id"],
            down_payment=18000,
            installments=0,
            monthly=0,
            total=18000,
        )
        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "make_payment",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
                "payment_type": "down_payment",
                "installment_number": None,
            }),
        ])

        response = orchestrator.handle_customer_message(
            "Peşinatımı öde", account, {}
        )

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["payment_result"]["amount"], 18000)
        self.assertIn("ödeme alındı", response["reply"])
        self.assertEqual(bank_tools.get_bank_balance(account["debtor_id"]), 2000)

    def test_account_agent_reports_balance_and_latest_payment(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        db_tools.save_payment_plan(
            debtor_id=account["debtor_id"],
            down_payment=18000,
            installments=0,
            monthly=0,
            total=18000,
        )
        plan = db_tools.get_payment_plan_history(account["debtor_id"])[0]
        payment = bank_tools.pay_payment_plan(
            account["debtor_id"], plan["id"], "down_payment"
        )
        self.assertEqual(payment["status"], "success")

        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "account_inquiry",
                "installments": None,
                "down_payment": None,
                "plan_number": None,
                "payment_type": None,
                "installment_number": None,
            }),
        ])

        response = orchestrator.handle_customer_message(
            "Hesabımda ne kadar bakiye kaldı, son işlemimi ne zaman yaptım?",
            account,
            {},
        )

        activity = response["account_activity"]
        self.assertEqual(response["status"], "success")
        self.assertEqual(activity["balance"], 2000)
        self.assertEqual(len(activity["transactions"]), 1)
        transaction = activity["transactions"][0]
        self.assertEqual(transaction["description"], "Peşinat ödemesi")
        self.assertEqual(transaction["amount"], 18000)
        self.assertIn(transaction["created_at"], response["reply"])
        self.assertIn("Hesap bakiyeniz: 2,000.00 TL", response["reply"])

    def test_insufficient_balance_does_not_record_or_apply_payment(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        db_tools.save_payment_plan(
            debtor_id=account["debtor_id"],
            down_payment=18000,
            installments=0,
            monthly=0,
            total=18000,
        )
        payment_plan_id = db_tools.get_payment_plan_history(account["debtor_id"])[0]["id"]
        with closing(sqlite3.connect(self.bank_db_path)) as conn:
            conn.execute(
                "UPDATE bank_accounts SET balance = 17999 WHERE debtor_id = ?",
                (account["debtor_id"],),
            )
            conn.commit()

        result = bank_tools.pay_payment_plan(
            account["debtor_id"], payment_plan_id, "down_payment"
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("Bakiye yetersiz", result["message"])
        self.assertEqual(bank_tools.get_bank_balance(account["debtor_id"]), 17999)
        self.assertFalse(bank_tools.has_paid_down_payment(payment_plan_id))
        debt_result = db_tools.get_customer_debts(account["debtor_id"])
        self.assertTrue(all(item["status"] == "AGREED" for item in debt_result["debt_accounts"]))

    def test_installment_requires_paid_down_payment_and_due_date(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        db_tools.save_payment_plan(
            debtor_id=account["debtor_id"],
            down_payment=4000,
            installments=2,
            monthly=5000,
            total=14000,
        )
        plan = db_tools.get_payment_plan_history(account["debtor_id"])[0]

        unpaid_down_payment_result = bank_tools.pay_payment_plan(
            account["debtor_id"], plan["id"], "installment"
        )
        self.assertIn("önce peşinat", unpaid_down_payment_result["message"])

        down_payment_result = bank_tools.pay_payment_plan(
            account["debtor_id"], plan["id"], "down_payment"
        )
        self.assertEqual(down_payment_result["status"], "success")
        not_due_result = bank_tools.pay_payment_plan(
            account["debtor_id"], plan["id"], "installment"
        )
        self.assertIn("henüz ödeme alınamaz", not_due_result["message"])

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE payment_plan_installments
                SET due_date = date('now')
                WHERE payment_plan_id = ? AND installment_number = 1
                """,
                (plan["id"],),
            )
            conn.commit()
        installment_result = bank_tools.pay_payment_plan(
            account["debtor_id"], plan["id"], "installment"
        )

        self.assertEqual(installment_result["status"], "success")
        self.assertEqual(installment_result["installment_number"], 1)
        updated_plan = db_tools.get_payment_plan_history(account["debtor_id"])[0]
        self.assertEqual(updated_plan["installments"][0]["status"], "PAID")

    def test_chat_creates_custom_offer_from_natural_language_values(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        orchestrator = DebtCollectionOrchestrator()
        orchestrator.llm = FakeLLM([
            json.dumps({
                "intent": "custom_plan",
                "installments": 4,
                "down_payment": 10000,
                "plan_number": None,
            }),
            json.dumps({"summary": "Dört taksitli özel teklif hazırlandı."}),
            json.dumps({"reply": "4 taksit ve 10.000 TL peşinatlı teklif aşağıda."}),
        ])

        response = orchestrator.handle_customer_message(
            "4 taksit ve 10.000 TL peşinatla teklif hazırla",
            account,
            {},
        )

        self.assertEqual(response["status"], "success")
        custom_plan = response["plan_result"]["plans"][-1]
        self.assertEqual(custom_plan["installments"], 4)
        self.assertEqual(custom_plan["down_payment"], 10000)

    def test_customer_session_is_free_text_not_a_numbered_menu(self):
        class FakeOrchestrator:
            llm = object()

            def __init__(self):
                self.messages = []

            def handle_customer_message(self, message, account, state):
                self.messages.append(message)
                self.account = account
                self.state = state
                return {
                    "status": "exit" if message == "çıkış" else "success",
                    "reply": "Görüşmek üzere." if message == "çıkış" else "Borç hesaplarınız burada.",
                }

        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        orchestrator = FakeOrchestrator()
        output = io.StringIO()
        with patch(
            "builtins.input",
            side_effect=["Borç bilgilerimi görüntüle", "çıkış"],
        ), contextlib.redirect_stdout(output):
            customer_session(orchestrator, account)

        self.assertEqual(
            orchestrator.messages,
            ["Borç bilgilerimi görüntüle", "çıkış"],
        )
        self.assertEqual(orchestrator.account["debtor_id"], account["debtor_id"])
        self.assertIsNotNone(orchestrator.state)
        self.assertNotIn("İşlem seçin", output.getvalue())

    def test_console_reads_password_without_echoing_it(self):
        account = db_tools.authenticate_customer("100001", "Ahmet123!")
        orchestrator = SimpleNamespace(
            llm=object(),
            handle_customer_message=lambda *_: {
                "status": "exit",
                "reply": "Görüşmek üzere.",
            },
        )
        output = io.StringIO()
        with (
            patch("main.init_db"),
            patch("main.DebtCollectionOrchestrator", return_value=orchestrator),
            patch("builtins.input", side_effect=["100001", "X", "X"]),
            patch("main.getpass", return_value="Ahmet123!") as hidden_password,
            patch("main.authenticate_customer", return_value=account) as authenticate,
            contextlib.redirect_stdout(output),
        ):
            run_console()

        hidden_password.assert_called_once_with("Şifre: ")
        authenticate.assert_called_once_with("100001", "Ahmet123!")
        self.assertNotIn("Ahmet123!", output.getvalue())


if __name__ == "__main__":
    unittest.main()

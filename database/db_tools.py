import sqlite3
import calendar
from contextlib import closing
from datetime import date
from config.settings import DB_PATH
from database.auth import verify_password


def authenticate_customer(customer_no: str, password: str) -> dict:
    """Müşteri numarası ve parolayı doğrular, bağlı borçlu kaydını döndürür."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute('''
            SELECT accounts.debtor_id, accounts.password_hash, debtors.tc_no, debtors.full_name
            FROM customer_accounts AS accounts
            JOIN debtors ON debtors.id = accounts.debtor_id
            WHERE accounts.customer_no = ?
        ''', (customer_no,)).fetchone()

    if not row or not verify_password(password, row[1]):
        return {"status": "error", "message": "Müşteri numarası veya şifre hatalı."}
    return {"status": "success", "debtor_id": row[0], "tc_no": row[2], "full_name": row[3]}


def get_payment_plan_history(debtor_id: int) -> list:
    """Borçlu için kayıtlı planları ve taksit takvimlerini getirir."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        plans = conn.execute('''
            SELECT id, down_payment, installment_count, monthly_payment, total_agreed_amount, created_at
            FROM payment_plans WHERE debtor_id = ? ORDER BY id DESC
        ''', (debtor_id,)).fetchall()
        history = []
        for plan in plans:
            installments = conn.execute('''
                SELECT installment_number, due_date, amount, status
                FROM payment_plan_installments WHERE payment_plan_id = ?
                ORDER BY installment_number
            ''', (plan["id"],)).fetchall()
            history.append({**dict(plan), "installments": [dict(item) for item in installments]})
    return history

def get_debtor_info(tc_no: str) -> dict:
    """Borçlunun anapara, faiz ve masraf detaylarını veritabanından getirir."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT id, full_name, status FROM debtors WHERE tc_no = ?",
            (tc_no,),
        ).fetchone()

    if not row:
        return {"status": "error", "message": f"{tc_no} TC numaralı borçlu bulunamadı."}

    debtor_id, name, debt_status = row
    debt_accounts = get_customer_debts(debtor_id)
    if debt_accounts["status"] == "error":
        return debt_accounts

    return {
        "status": "success",
        "debtor_id": debtor_id,
        "debt_status": debt_status,
        "full_name": name,
        "principal_debt": debt_accounts["principal_debt"],
        "accrued_interest": debt_accounts["accrued_interest"],
        "legal_costs": debt_accounts["legal_costs"],
        "total_debt": debt_accounts["total_debt"],
        "debt_accounts": debt_accounts["debt_accounts"],
    }

def get_customer_debts(debtor_id: int) -> dict:
    """Müşterinin kredi/borç ürünlerini ve toplamlarını getirir."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''
            SELECT product_name, principal_debt, accrued_interest, legal_costs, status
            FROM debt_accounts
            WHERE debtor_id = ?
            ORDER BY id
        ''', (debtor_id,)).fetchall()

    if not rows:
        return {"status": "error", "message": "Müşteriye ait borç hesabı bulunamadı."}

    debt_accounts = [dict(row) for row in rows]
    principal = sum(account["principal_debt"] for account in debt_accounts)
    interest = sum(account["accrued_interest"] for account in debt_accounts)
    costs = sum(account["legal_costs"] for account in debt_accounts)
    return {
        "status": "success",
        "debt_accounts": debt_accounts,
        "principal_debt": principal,
        "accrued_interest": interest,
        "legal_costs": costs,
        "total_debt": principal + interest + costs,
    }

def get_policy_rules(total_debt: float) -> dict:
    """Borç tutarına göre uygulanacak minumum peşinat, max taksit ve faiz indirimi kurallarını çeker."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT min_down_payment_rate, max_installment_count, max_interest_discount_rate 
        FROM repayment_policies 
        WHERE ? BETWEEN min_debt AND max_debt
    ''', (total_debt,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {"status": "error", "message": "Borç tutarına uygun politika bulunamadı."}

    min_rate, max_installments, max_discount = row
    return {
        "status": "success",
        "min_down_payment_rate": min_rate,
        "min_down_payment_amount": total_debt * min_rate,
        "max_installment_count": max_installments,
        "max_interest_discount_rate": max_discount
    }

def save_payment_plan(debtor_id: int, down_payment: float, installments: int, monthly: float, total: float) -> dict:
    """Onaylanan ödeme planını veritabanına kaydeder."""
    if total <= 0 or down_payment < 0 or down_payment > total:
        return {"status": "error", "message": "Peşinat veya toplam borç tutarı geçersiz."}
    if installments < 0 or (installments == 0 and down_payment != total):
        return {"status": "error", "message": "Taksitsiz planda toplam tutar peşinat olarak ödenmelidir."}
    if installments > 0 and (
        monthly <= 0 or abs(down_payment + monthly * installments - total) > 0.01
    ):
        return {"status": "error", "message": "Taksit bilgileri geçersiz."}

    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.cursor()
        existing_plan = cursor.execute(
            "SELECT id FROM payment_plans WHERE debtor_id = ? LIMIT 1",
            (debtor_id,),
        ).fetchone()
        if existing_plan:
            return {"status": "error", "message": "Bu müşteri için kayıtlı bir ödeme planı zaten var."}

        cursor.execute('''
            INSERT INTO payment_plans (debtor_id, down_payment, installment_count, monthly_payment, total_agreed_amount)
            VALUES (?, ?, ?, ?, ?)
        ''', (debtor_id, down_payment, installments, monthly, total))
        payment_plan_id = cursor.lastrowid

        today = date.today()
        for installment_number in range(1, installments + 1):
            month_index = today.month - 1 + installment_number
            due_year = today.year + month_index // 12
            due_month = month_index % 12 + 1
            due_day = min(today.day, calendar.monthrange(due_year, due_month)[1])
            due_date = date(due_year, due_month, due_day).isoformat()
            amount = round(total - down_payment - monthly * (installments - 1), 2) if installment_number == installments else monthly
            cursor.execute('''
                INSERT INTO payment_plan_installments
                (payment_plan_id, installment_number, due_date, amount)
                VALUES (?, ?, ?, ?)
            ''', (payment_plan_id, installment_number, due_date, amount))

        cursor.execute("UPDATE debtors SET status = 'AGREED' WHERE id = ?", (debtor_id,))
        cursor.execute(
            "UPDATE debt_accounts SET status = 'AGREED' WHERE debtor_id = ?",
            (debtor_id,),
        )
        conn.commit()
    return {"status": "success", "message": "Ödeme planı veritabanına kaydedildi."}

import sqlite3
from contextlib import closing
from datetime import date

from config.settings import BANK_DB_PATH as DEFAULT_BANK_DB_PATH
from database.db_tools import DB_PATH as DEFAULT_DEBT_DB_PATH

BANK_DB_PATH = DEFAULT_BANK_DB_PATH
DB_PATH = DEFAULT_DEBT_DB_PATH


def init_bank_accounts(customer_accounts: list[tuple[int, str]]) -> None:
    """Create demo bank accounts once; later application starts preserve balances."""
    with closing(sqlite3.connect(BANK_DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_accounts (
                debtor_id INTEGER PRIMARY KEY,
                customer_no TEXT NOT NULL UNIQUE,
                balance REAL NOT NULL CHECK (balance >= 0)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debtor_id INTEGER NOT NULL,
                payment_plan_id INTEGER NOT NULL,
                payment_plan_installment_id INTEGER,
                payment_type TEXT NOT NULL CHECK (payment_type IN ('down_payment', 'installment')),
                amount REAL NOT NULL CHECK (amount > 0),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_down_payment_once
            ON bank_transactions(payment_plan_id)
            WHERE payment_type = 'down_payment'
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_installment_once
            ON bank_transactions(payment_plan_installment_id)
            WHERE payment_type = 'installment'
        """)
        conn.executemany(
            """
            INSERT OR IGNORE INTO bank_accounts (debtor_id, customer_no, balance)
            VALUES (?, ?, ?)
            """,
            [
                (debtor_id, customer_no, _demo_balance(customer_no))
                for debtor_id, customer_no in customer_accounts
            ],
        )
        conn.commit()


def _demo_balance(customer_no: str) -> float:
    from config.settings import DEMO_ACCOUNT_BALANCES

    return DEMO_ACCOUNT_BALANCES.get(customer_no, 0.0)


def get_bank_balance(debtor_id: int) -> float | None:
    with closing(sqlite3.connect(BANK_DB_PATH)) as conn:
        row = conn.execute(
            "SELECT balance FROM bank_accounts WHERE debtor_id = ?",
            (debtor_id,),
        ).fetchone()
    return row[0] if row else None


def get_account_activity(debtor_id: int, limit: int = 10) -> dict:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Hesap hareketi limiti 1 ile 100 arasında olmalıdır.")

    with closing(sqlite3.connect(BANK_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS debts", (DB_PATH,))
        account = conn.execute(
            """
            SELECT customer_no, balance
            FROM bank_accounts
            WHERE debtor_id = ?
            """,
            (debtor_id,),
        ).fetchone()
        if not account:
            return {"status": "error", "message": "Banka hesabı bulunamadı."}

        transactions = conn.execute(
            """
            SELECT
                transactions.id,
                transactions.payment_plan_id,
                transactions.payment_type,
                transactions.amount,
                transactions.created_at,
                installments.installment_number
            FROM bank_transactions AS transactions
            LEFT JOIN debts.payment_plan_installments AS installments
                ON installments.id = transactions.payment_plan_installment_id
            WHERE transactions.debtor_id = ?
            ORDER BY transactions.created_at DESC, transactions.id DESC
            LIMIT ?
            """,
            (debtor_id, limit),
        ).fetchall()

    return {
        "status": "success",
        "customer_no": account["customer_no"],
        "balance": account["balance"],
        "transactions": [
            {
                **dict(transaction),
                "description": (
                    "Peşinat ödemesi"
                    if transaction["payment_type"] == "down_payment"
                    else f"{transaction['installment_number']}. taksit ödemesi"
                ),
            }
            for transaction in transactions
        ],
    }


def has_paid_down_payment(payment_plan_id: int) -> bool:
    with closing(sqlite3.connect(BANK_DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM bank_transactions
            WHERE payment_plan_id = ? AND payment_type = 'down_payment'
            """,
            (payment_plan_id,),
        ).fetchone()
    return row is not None


def pay_payment_plan(
    debtor_id: int,
    payment_plan_id: int,
    payment_type: str,
    installment_number: int | None = None,
) -> dict:
    if payment_type not in {"down_payment", "installment"}:
        return {"status": "error", "message": "Ödeme türü geçersiz."}
    if payment_type == "down_payment" and installment_number is not None:
        return {"status": "error", "message": "Peşinat ödemesinde taksit numarası kullanılmaz."}
    if payment_type == "installment" and installment_number is not None and (
        type(installment_number) is not int or installment_number < 1
    ):
        return {"status": "error", "message": "Taksit numarası geçersiz."}

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("ATTACH DATABASE ? AS bank", (BANK_DB_PATH,))
        conn.execute("BEGIN IMMEDIATE")
        plan = conn.execute(
            """
            SELECT down_payment, installment_count, total_agreed_amount
            FROM payment_plans
            WHERE id = ? AND debtor_id = ?
            """,
            (payment_plan_id, debtor_id),
        ).fetchone()
        if not plan:
            conn.rollback()
            return {"status": "error", "message": "Kayıtlı ödeme planı bulunamadı."}

        if payment_type == "down_payment":
            amount = plan[0]
            if amount <= 0:
                conn.rollback()
                return {"status": "error", "message": "Bu plan için ödenecek peşinat bulunmuyor."}
            already_paid = conn.execute(
                """
                SELECT 1 FROM bank.bank_transactions
                WHERE payment_plan_id = ? AND payment_type = 'down_payment'
                """,
                (payment_plan_id,),
            ).fetchone()
            if already_paid:
                conn.rollback()
                return {"status": "error", "message": "Bu planın peşinatı daha önce ödenmiş."}
            installment_id = None
        else:
            if plan[1] == 0:
                conn.rollback()
                return {"status": "error", "message": "Bu peşin kapatma planında taksit bulunmuyor."}
            down_payment_paid = conn.execute(
                """
                SELECT 1 FROM bank.bank_transactions
                WHERE payment_plan_id = ? AND payment_type = 'down_payment'
                """,
                (payment_plan_id,),
            ).fetchone()
            if not down_payment_paid:
                conn.rollback()
                return {"status": "error", "message": "Taksit ödemeden önce peşinat ödenmelidir."}
            if installment_number is None:
                installment = conn.execute(
                    """
                    SELECT id, installment_number, amount, due_date
                    FROM payment_plan_installments
                    WHERE payment_plan_id = ? AND status = 'PENDING'
                    ORDER BY installment_number
                    LIMIT 1
                    """,
                    (payment_plan_id,),
                ).fetchone()
            else:
                installment = conn.execute(
                    """
                    SELECT id, installment_number, amount, due_date
                    FROM payment_plan_installments
                    WHERE payment_plan_id = ? AND installment_number = ?
                      AND status = 'PENDING'
                    """,
                    (payment_plan_id, installment_number),
                ).fetchone()
            if not installment:
                conn.rollback()
                return {"status": "error", "message": "Ödenmemiş uygun bir taksit bulunamadı."}
            first_pending = conn.execute(
                """
                SELECT installment_number FROM payment_plan_installments
                WHERE payment_plan_id = ? AND status = 'PENDING'
                ORDER BY installment_number
                LIMIT 1
                """,
                (payment_plan_id,),
            ).fetchone()
            if installment[1] != first_pending[0]:
                conn.rollback()
                return {
                    "status": "error",
                    "message": f"Önce {first_pending[0]}. taksidi ödemeniz gerekir.",
                }
            if installment_number is None and installment[3] > date.today().isoformat():
                conn.rollback()
                return {
                    "status": "error",
                    "message": f"İlk taksidin vadesi {installment[3]} tarihinde; henüz ödeme alınamaz.",
                }
            if installment_number is not None and installment[3] > date.today().isoformat():
                conn.rollback()
                return {
                    "status": "error",
                    "message": f"{installment_number}. taksidin vadesi {installment[3]} tarihinde; henüz ödeme alınamaz.",
                }
            installment_id = installment[0]
            amount = installment[2]

        balance_row = conn.execute(
            "SELECT balance FROM bank.bank_accounts WHERE debtor_id = ?",
            (debtor_id,),
        ).fetchone()
        if not balance_row:
            conn.rollback()
            return {"status": "error", "message": "Müşterinin banka hesabı bulunamadı."}
        balance = balance_row[0]
        if balance < amount:
            conn.rollback()
            return {
                "status": "error",
                "message": (
                    f"Bakiye yetersiz. Ödenecek tutar {amount:,.2f} TL, "
                    f"mevcut bakiye {balance:,.2f} TL."
                ),
                "balance": balance,
                "amount": amount,
            }

        cursor = conn.execute(
            """
            UPDATE bank.bank_accounts
            SET balance = balance - ?
            WHERE debtor_id = ? AND balance >= ?
            """,
            (amount, debtor_id, amount),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return {
                "status": "error",
                "message": "Bakiye ödeme sırasında değişti; işlem yapılmadı.",
            }

        conn.execute(
            """
            INSERT INTO bank.bank_transactions
            (debtor_id, payment_plan_id, payment_plan_installment_id, payment_type, amount)
            VALUES (?, ?, ?, ?, ?)
            """,
            (debtor_id, payment_plan_id, installment_id, payment_type, amount),
        )
        if installment_id is not None:
            conn.execute(
                "UPDATE payment_plan_installments SET status = 'PAID' WHERE id = ?",
                (installment_id,),
            )
        remaining_installments = conn.execute(
            """
            SELECT COUNT(*) FROM payment_plan_installments
            WHERE payment_plan_id = ? AND status != 'PAID'
            """,
            (payment_plan_id,),
        ).fetchone()[0]
        down_payment_paid = payment_type == "down_payment" or conn.execute(
            """
            SELECT 1 FROM bank.bank_transactions
            WHERE payment_plan_id = ? AND payment_type = 'down_payment'
            """,
            (payment_plan_id,),
        ).fetchone() is not None
        if down_payment_paid and remaining_installments == 0:
            conn.execute(
                "UPDATE debtors SET status = 'CLOSED' WHERE id = ?",
                (debtor_id,),
            )
            conn.execute(
                "UPDATE debt_accounts SET status = 'CLOSED' WHERE debtor_id = ?",
                (debtor_id,),
            )

        new_balance = balance - amount
        conn.commit()
        return {
            "status": "success",
            "amount": amount,
            "balance": new_balance,
            "payment_type": payment_type,
            "installment_number": installment[1] if payment_type == "installment" else None,
            "plan_closed": down_payment_paid and remaining_installments == 0,
        }
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()

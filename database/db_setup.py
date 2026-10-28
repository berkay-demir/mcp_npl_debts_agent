import sqlite3
from contextlib import closing
from database.auth import hash_password
from config.settings import DB_PATH


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Borçlular Tablosu
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS debtors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tc_no TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        principal_debt REAL NOT NULL, -- Anapara
        accrued_interest REAL NOT NULL, -- İşleyen Faiz
        legal_costs REAL NOT NULL, -- Yasal/İcra Masrafları
        status TEXT DEFAULT 'ACTIVE' -- ACTIVE, CLOSED, AGREED
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS customer_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_no TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        debtor_id INTEGER UNIQUE NOT NULL,
        FOREIGN KEY(debtor_id) REFERENCES debtors(id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS debt_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        debtor_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        principal_debt REAL NOT NULL,
        accrued_interest REAL NOT NULL,
        legal_costs REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        FOREIGN KEY(debtor_id) REFERENCES debtors(id)
    )
    ''')

    # 2. Kademeli Yapılandırma Politikaları Tablosu
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS repayment_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        min_debt REAL NOT NULL,
        max_debt REAL NOT NULL,
        min_down_payment_rate REAL NOT NULL, -- Örn: 0.20 (%20)
        max_installment_count INTEGER NOT NULL, -- Örn: 6 taksit
        max_interest_discount_rate REAL NOT NULL -- Peşin ödemede faiz indirim üst sınırı
    )
    ''')

    # 3. Anlaşma & Taksit Kayıtları Tablosu
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payment_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        debtor_id INTEGER,
        down_payment REAL,
        installment_count INTEGER,
        monthly_payment REAL,
        total_agreed_amount REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(debtor_id) REFERENCES debtors(id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payment_plan_installments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_plan_id INTEGER NOT NULL,
        installment_number INTEGER NOT NULL,
        due_date TEXT NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        FOREIGN KEY(payment_plan_id) REFERENCES payment_plans(id)
    )
    ''')

    # Örnek Borçlular (Farklı borç kademelerinde)
    debtors_data = [
        ('11111111111', 'Ahmet Yılmaz', 15000.0, 3000.0, 1500.0),   # Toplam: 19,500 TL (Düşük Borç)
        ('22222222222', 'Ayşe Kaya', 60000.0, 12000.0, 5000.0),     # Toplam: 77,000 TL (Orta Borç)
        ('33333333333', 'Mehmet Demir', 250000.0, 45000.0, 15000.0)  # Toplam: 310,000 TL (Yüksek Borç)
    ]
    cursor.executemany('''
    INSERT OR IGNORE INTO debtors (tc_no, full_name, principal_debt, accrued_interest, legal_costs)
    VALUES (?, ?, ?, ?, ?)
    ''', debtors_data)

    demo_debt_accounts = {
        '11111111111': [
            ('Ek Hesap', 5000.0, 800.0, 500.0),
            ('Kredi Kartı', 5000.0, 1200.0, 500.0),
            ('İhtiyaç Kredisi', 5000.0, 1000.0, 500.0),
        ],
        '22222222222': [
            ('Ek Hesap', 10000.0, 2000.0, 1000.0),
            ('Kredi Kartı', 20000.0, 4000.0, 1500.0),
            ('İhtiyaç Kredisi', 30000.0, 6000.0, 2500.0),
        ],
        '33333333333': [
            ('Ek Hesap', 30000.0, 6000.0, 2000.0),
            ('Kredi Kartı', 70000.0, 15000.0, 5000.0),
            ('İhtiyaç Kredisi', 150000.0, 24000.0, 8000.0),
        ],
    }
    for tc_no, accounts in demo_debt_accounts.items():
        debtor_row = cursor.execute(
            "SELECT id, status, principal_debt, accrued_interest, legal_costs "
            "FROM debtors WHERE tc_no = ?",
            (tc_no,),
        ).fetchone()
        if debtor_row:
            has_accounts = cursor.execute(
                "SELECT 1 FROM debt_accounts WHERE debtor_id = ? LIMIT 1",
                (debtor_row[0],),
            ).fetchone()
            account_totals = (
                sum(account[1] for account in accounts),
                sum(account[2] for account in accounts),
                sum(account[3] for account in accounts),
            )
            if not has_accounts and account_totals == tuple(debtor_row[2:5]):
                cursor.executemany('''
                    INSERT INTO debt_accounts
                    (debtor_id, product_name, principal_debt, accrued_interest, legal_costs, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', [
                    (debtor_row[0], name, principal, interest, costs, debtor_row[1])
                    for name, principal, interest, costs in accounts
                ])

    cursor.execute('''
        INSERT INTO debt_accounts
        (debtor_id, product_name, principal_debt, accrued_interest, legal_costs, status)
        SELECT debtors.id, 'Diğer Borç', debtors.principal_debt,
               debtors.accrued_interest, debtors.legal_costs, debtors.status
        FROM debtors
        WHERE NOT EXISTS (
            SELECT 1 FROM debt_accounts WHERE debt_accounts.debtor_id = debtors.id
        )
    ''')

    # Kademeli Politikalar Matrisi
    policies_data = [
        (0.0, 25000.0, 0.20, 6, 0.50),       # 0 - 25.000 TL: Min %20 Peşinat, Max 6 Taksit, Peşinde %50 Faiz İndirimi
        (25000.01, 100000.0, 0.15, 12, 0.35), # 25.000 - 100.000 TL: Min %15 Peşinat, Max 12 Taksit, Peşinde %35 Faiz İndirimi
        (100000.01, 9999999.0, 0.10, 24, 0.25) # 100.000+ TL: Min %10 Peşinat, Max 24 Taksit, Peşinde %25 Faiz İndirimi
    ]
    cursor.execute("SELECT COUNT(*) FROM repayment_policies")
    if cursor.fetchone()[0] == 0:
        cursor.executemany('''
        INSERT INTO repayment_policies
        (min_debt, max_debt, min_down_payment_rate, max_installment_count, max_interest_discount_rate)
        VALUES (?, ?, ?, ?, ?)
        ''', policies_data)

    demo_accounts = [
        ("100001", "Ahmet123!", "11111111111"),
        ("100002", "Ayse123!", "22222222222"),
        ("100003", "Mehmet123!", "33333333333"),
    ]
    for customer_no, password, tc_no in demo_accounts:
        cursor.execute("SELECT id FROM debtors WHERE tc_no = ?", (tc_no,))
        debtor_row = cursor.fetchone()
        if debtor_row:
            cursor.execute('''
                INSERT OR IGNORE INTO customer_accounts (customer_no, password_hash, debtor_id)
                VALUES (?, ?, ?)
            ''', (customer_no, hash_password(password), debtor_row[0]))

    conn.commit()
    conn.close()
    from database.bank_tools import init_bank_accounts

    with closing(sqlite3.connect(DB_PATH)) as conn:
        customer_accounts = conn.execute(
            "SELECT debtor_id, customer_no FROM customer_accounts"
        ).fetchall()
    init_bank_accounts(customer_accounts)
    print("Veritabanı, demo müşteri hesapları ve kademeli politika matrisi hazır.")

if __name__ == "__main__":
    init_db()

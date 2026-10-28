import os
from dotenv import load_dotenv

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
DB_PATH = os.getenv("DB_PATH", "debt_collection.db")
BANK_DB_PATH = os.getenv("BANK_DB_PATH", "bank_accounts.db")
DEMO_ACCOUNT_BALANCES = {
    "100001": 20000.0,
    "100002": 50000.0,
    "100003": 250000.0,
}

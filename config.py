import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "data" / "app_data.sqlite3"
QUESTION_FILE = BASE_DIR / "data" / "questions.json"
SECRET_FILE = BASE_DIR / "data" / ".jwt_secret"
PROMPT_PATH = BASE_DIR / "prompts" / "v1.0_system.txt"
QUESTION_PROMPT_PATH = BASE_DIR / "prompts" / "v1.0_question_generator.txt"

TOKEN_EXPIRY_HOURS = 24 * 7
SESSION_LENGTH = 5
QUESTION_SOURCE_AI = "ai"
QUESTION_SOURCE_FALLBACK = "fallback"

AI_PROVIDER = "groq"
AI_ENABLED = os.getenv("AI_ENABLED", "false").lower() == "true"
AI_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "30"))
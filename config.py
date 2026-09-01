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
HINT_PROMPT_PATH = BASE_DIR / "prompts" / "v1.0_hint.txt"
JD_PROMPT_PATH = BASE_DIR / "prompts" / "v1.0_jd_parser.txt"

JD_MAX_CHARS = 5000

EASY_MAX_WORDS = 35
MEDIUM_MAX_WORDS = 55
CONSTRAINT_SIGNALS = ("deadline", "legacy", "budget", "stakeholder", "meanwhile", "however")

TOKEN_EXPIRY_HOURS = 24 * 7
SESSION_LENGTH = 5
QUESTION_SOURCE_AI = "ai"
QUESTION_SOURCE_FALLBACK = "fallback"
QUESTION_SOURCE_FALLBACK_NO_JD = "fallback_no_jd_match"

AI_PROVIDER = "groq"
AI_ENABLED = os.getenv("AI_ENABLED", "false").lower() == "true"
AI_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "30"))
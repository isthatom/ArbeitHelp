import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]

# Hermetic runtime BEFORE app is imported: AI disabled, no provider key,
# stable JWT secret, and a throwaway database (real app_data.sqlite3 is never touched).
os.environ["AI_ENABLED"] = "false"
os.environ.pop("GROQ_API_KEY", None)
os.environ["JWT_SECRET"] = "test-secret-key-that-is-at-least-32-bytes-long"

_TMP_DIR = Path(tempfile.mkdtemp(prefix="arbeithelp_tests_"))
_DB_PATH = _TMP_DIR / "app_data.sqlite3"
_SECRET_PATH = _TMP_DIR / ".jwt_secret"

sys.path.insert(0, str(PROJECT))

import config  # noqa: E402

config.DB_PATH = _DB_PATH
config.SECRET_FILE = _SECRET_PATH


@pytest.fixture(scope="session")
def client():
    import app

    # app binds these globals at import time; ensure they point at the temp DB.
    app.DB_PATH = _DB_PATH
    app.SECRET_FILE = _SECRET_PATH
    app._init_db()
    app._migrate_db()
    with app.app.test_client() as c:
        yield c
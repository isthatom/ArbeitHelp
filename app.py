import hashlib
import json
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from io import BytesIO

import jwt
from flask import Flask, Response, jsonify, request, send_from_directory

from ai_teacher import ai_correct, ai_generate_question, ai_grade, ai_model_answer
from config import (
    AI_ENABLED,
    AI_MODEL,
    DB_PATH,
    QUESTION_FILE,
    QUESTION_SOURCE_AI,
    QUESTION_SOURCE_FALLBACK,
    SECRET_FILE,
    SESSION_LENGTH,
    STATIC_DIR,
    TOKEN_EXPIRY_HOURS,
)


def _load_secret() -> str:
    env_secret = os.getenv("JWT_SECRET")
    if env_secret:
        return env_secret
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = os.urandom(32).hex()
    SECRET_FILE.write_text(secret, encoding="utf-8")
    return secret


SECRET_KEY = _load_secret()

app = Flask(__name__, static_folder=None)

with open(QUESTION_FILE, "r", encoding="utf-8") as f:
    ROLE_DATA = json.load(f)["roles"]
QUESTIONS = {role: data["questions"] for role, data in ROLE_DATA.items()}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_email TEXT NOT NULL,
                role TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                current_q_index INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                finalized INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(user_email) REFERENCES users(email)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_email TEXT NOT NULL,
                role TEXT NOT NULL,
                question_text TEXT NOT NULL,
                question_index INTEGER NOT NULL,
                answer TEXT,
                points INTEGER,
                feedback TEXT,
                breakdown TEXT,
                grader TEXT,
                ai_used INTEGER NOT NULL DEFAULT 0,
                fallback_reason TEXT,
                skipped INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            """
        )


_init_db()


def _migrate_db():
    with _db() as conn:
        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(attempts)").fetchall()
        }
        columns_to_add = {
            "question_source": "TEXT NOT NULL DEFAULT 'fallback'",
            "question_topic": "TEXT",
            "question_difficulty": "TEXT",
            "generation_error": "TEXT",
            "model_answer": "TEXT",
        }
        for column_name, ddl in columns_to_add.items():
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE attempts ADD COLUMN {column_name} {ddl}")


_migrate_db()


def _utcnow():
    return datetime.now(timezone.utc)


def _iso_now():
    return _utcnow().isoformat()


def _hash_pw(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def _make_token(user_id):
    payload = {"user_id": user_id, "iat": _utcnow(), "exp": _utcnow() + timedelta(hours=TOKEN_EXPIRY_HOURS)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def _verify_token(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])["user_id"]
    except jwt.PyJWTError:
        return None


def _auth_header():
    return request.headers.get("Authorization") or request.headers.get("Authorisation") or ""


def _get_token_user():
    auth = _auth_header()
    if auth.startswith("Bearer "):
        return _verify_token(auth[7:])
    return None


def _current_session_token():
    auth = _auth_header()
    if auth.startswith("Bearer "):
        token = auth[7:]
        with _db() as conn:
            row = conn.execute("SELECT id FROM sessions WHERE id = ?", (token,)).fetchone()
            if row:
                return token
    return None


def _check_user():
    user_id = _get_token_user()
    if not user_id:
        return None, (jsonify({"error": "not logged in"}), 401)
    return user_id, None


def _get_user_record(email):
    with _db() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def _load_session(session_id):
    with _db() as conn:
        return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def _require_session():
    token = _current_session_token()
    if not token:
        return None, (jsonify({"error": "invalid session"}), 401)
    session = _load_session(token)
    if not session:
        return None, (jsonify({"error": "invalid session"}), 401)
    user_id = _get_token_user()
    if user_id != session["user_email"]:
        return None, (jsonify({"error": "session ownership mismatch"}), 403)
    return session, None


def _session_attempts(session_id):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM attempts WHERE session_id = ? ORDER BY question_index ASC, id ASC",
            (session_id,),
        ).fetchall()


def _questions_for_role(role):
    return QUESTIONS.get(role, [])


def _normalize_text(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _session_state(session, attempts):
    answered_count = int(session["current_q_index"]) if session else 0
    total_points = sum(int(a["points"] or 0) for a in attempts if not a["skipped"] and a["points"] is not None)
    return {
        "question_number": answered_count + 1 if session and not session["finalized"] else SESSION_LENGTH,
        "questions_answered": answered_count,
        "session_length": SESSION_LENGTH,
        "session_score": total_points,
        "session_completed": bool(session and session["finalized"]),
    }


def _session_progress(session_id):
    return _session_state(_load_session(session_id), _session_attempts(session_id))


def _question_source_label(source):
    if source == QUESTION_SOURCE_AI:
        return "AI-generated"
    return "Fallback question bank"


def _generation_context(attempts):
    return [
        {
            "question_number": int(attempt["question_index"]) + 1,
            "question": attempt["question_text"],
            "answer": attempt["answer"],
            "feedback": attempt["feedback"],
            "points": attempt["points"],
            "grader": attempt["grader"],
            "question_source": attempt["question_source"],
            "topic": attempt["question_topic"],
            "difficulty": attempt["question_difficulty"],
            "skipped": bool(attempt["skipped"]),
        }
        for attempt in attempts
    ]


def _validate_generated_question(candidate, used_questions):
    question = str(candidate.get("question", "")).strip()
    topic = str(candidate.get("topic", "")).strip()
    difficulty = str(candidate.get("difficulty", "")).strip().lower()
    if not question or not topic or difficulty not in {"easy", "medium", "hard"}:
        return None
    normalized = _normalize_text(question)
    if not normalized:
        return None
    if normalized in used_questions:
        return None
    return {
        "question": question,
        "topic": topic,
        "difficulty": difficulty,
    }


def _fallback_question(role, used_questions):
    pool = _questions_for_role(role)
    available = [q for q in pool if _normalize_text(q["q"]) not in used_questions]
    if not available:
        available = pool
    if not available:
        return None
    chosen = random.choice(available)
    return {
        "question": chosen["q"],
        "topic": chosen.get("topic") or chosen.get("concepts", ["general"])[0],
        "difficulty": _difficulty(chosen.get("ideal_length", 80)),
        "source": QUESTION_SOURCE_FALLBACK,
        "generation_error": "ai_generation_unavailable",
    }


def _store_question_attempt(session, question_payload, generation_error=None):
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO attempts (
                session_id, user_email, role, question_text, question_index, question_source,
                question_topic, question_difficulty, generation_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                session["user_email"],
                session["role"],
                question_payload["question"],
                int(session["current_q_index"]),
                question_payload.get("source", QUESTION_SOURCE_AI),
                question_payload.get("topic"),
                question_payload.get("difficulty"),
                generation_error,
                _iso_now(),
            ),
        )


def _question_response(session, question_payload, source, session_state, resume=False):
    payload = {
        "question": question_payload["question"],
        "role": session["role"],
        "topic": question_payload.get("topic"),
        "difficulty": question_payload.get("difficulty"),
        "question_number": session_state["question_number"],
        "questions_answered": session_state["questions_answered"],
        "session_length": session_state["session_length"],
        "session_score": session_state["session_score"],
        "question_source": source,
        "question_source_label": _question_source_label(source),
        "session_completed": session_state["session_completed"],
    }
    if resume:
        payload["resume"] = True
    return payload


def _current_attempt(session_id, question_index):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM attempts WHERE session_id = ? AND question_index = ?",
            (session_id, question_index),
        ).fetchone()


def _next_question(session):
    index = int(session["current_q_index"])
    if index >= SESSION_LENGTH:
        return None, None

    attempts = _session_attempts(session["id"])

    used_questions = {_normalize_text(a["question_text"]) for a in attempts}
    context = _generation_context(attempts)
    generated = ai_generate_question(
        session["role"],
        index + 1,
        {
            "prior_questions": context,
            "used_questions": [a["question_text"] for a in attempts],
            "used_topics": [a["question_topic"] for a in attempts if a["question_topic"]],
            "used_difficulties": [a["question_difficulty"] for a in attempts if a["question_difficulty"]],
        },
        SESSION_LENGTH,
    )
    validated = _validate_generated_question(generated or {}, used_questions)
    if validated:
        return {
            "question": validated["question"],
            "topic": validated["topic"],
            "difficulty": validated["difficulty"],
            "source": QUESTION_SOURCE_AI,
        }, index

    fallback = _fallback_question(session["role"], used_questions)
    if not fallback:
        return None, None
    return fallback, index


def _difficulty(ideal_length):
    if ideal_length <= 85:
        return "easy"
    if ideal_length <= 100:
        return "medium"
    return "hard"


def _finalize_session(session_id):
    with _db() as conn:
        conn.execute(
            "UPDATE sessions SET finalized = 1, completed_at = COALESCE(completed_at, ?) WHERE id = ?",
            (_iso_now(), session_id),
        )


def _advance_session(conn, session_id, skipped=False):
    if skipped:
        conn.execute(
            "UPDATE sessions SET current_q_index = current_q_index + 1, skipped_count = skipped_count + 1 WHERE id = ?",
            (session_id,),
        )
    else:
        conn.execute(
            "UPDATE sessions SET current_q_index = current_q_index + 1 WHERE id = ?",
            (session_id,),
        )
    row = conn.execute("SELECT current_q_index FROM sessions WHERE id = ?", (session_id,)).fetchone()
    finalized = bool(row) and int(row["current_q_index"]) >= SESSION_LENGTH
    if finalized:
        conn.execute(
            "UPDATE sessions SET finalized = 1, completed_at = COALESCE(completed_at, ?) WHERE id = ?",
            (_iso_now(), session_id),
        )
    return finalized


def _session_summary(session_id):
    session = _load_session(session_id)
    attempts = _session_attempts(session_id)
    total_points = sum(int(a["points"] or 0) for a in attempts if not a["skipped"])
    return {
        "session_id": session_id,
        "role": session["role"] if session else None,
        "questions_answered": len([a for a in attempts if not a["skipped"]]),
        "skipped_count": int(session["skipped_count"]) if session else 0,
        "completed": bool(session and session["finalized"]),
        "session_score": total_points,
        "session_length": SESSION_LENGTH,
        "total_points": total_points,
        "questions": [
            {
                "question": a["question_text"],
                "answer": a["answer"],
                "points": a["points"],
                "feedback": a["feedback"],
                "breakdown": a["breakdown"],
                "grader": a["grader"],
                "ai_used": bool(a["ai_used"]),
                "skipped": bool(a["skipped"]),
                "question_source": a["question_source"],
                "question_source_label": _question_source_label(a["question_source"]),
                "topic": a["question_topic"],
                "difficulty": a["question_difficulty"],
            }
            for a in attempts
        ],
    }


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/auth/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    role = data.get("role", "").strip()
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    with _db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            return jsonify({"error": "email already registered"}), 400
        conn.execute(
            "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (email, _hash_pw(password), role, _iso_now()),
        )
    return jsonify({"token": _make_token(email), "role": role})


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    user = _get_user_record(email)
    if not user or user["password_hash"] != _hash_pw(password):
        return jsonify({"error": "invalid credentials"}), 401
    return jsonify({"token": _make_token(email), "role": user["role"]})


@app.route("/auth/me")
def me():
    user_id, err = _check_user()
    if err:
        return err
    user = _get_user_record(user_id)
    return jsonify({"email": user_id, "role": user["role"] if user else ""})


@app.route("/session/start", methods=["POST"])
def start_session():
    user_id, err = _check_user()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    role = data.get("role", "").strip()
    if role not in QUESTIONS:
        return jsonify({"error": "invalid role"}), 400
    token = _make_token(user_id)
    with _db() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_email, role, started_at, current_q_index, skipped_count, finalized) VALUES (?, ?, ?, ?, 0, 0, 0)",
            (token, user_id, role, _iso_now()),
        )
    return jsonify(
        {
            "session_token": token,
            "role": role,
            "session_length": SESSION_LENGTH,
            "current_question_number": 1,
            "questions_answered": 0,
            "session_score": 0,
            "session_completed": False,
        }
    )


@app.route("/session/question")
def session_question():
    session, err = _require_session()
    if err:
        return err
    if session["finalized"] or int(session["current_q_index"]) >= SESSION_LENGTH: # type: ignore
        return jsonify({"error": "session complete", "completed": True, "summary": _session_summary(session["id"])}), 409
    session_state = _session_state(session, _session_attempts(session["id"]))
    existing = _current_attempt(session["id"], int(session["current_q_index"]))
    if existing:
        return jsonify(
            _question_response(
                session,
                {
                    "question": existing["question_text"],
                    "topic": existing["question_topic"],
                    "difficulty": existing["question_difficulty"],
                },
                existing["question_source"],
                session_state,
                resume=True,
            )
        )
    chosen, _ = _next_question(session)
    if not chosen:
        _finalize_session(session["id"])
        return jsonify({"error": "session complete", "completed": True, "summary": _session_summary(session["id"])}), 409
    _store_question_attempt(session, chosen, chosen.get("generation_error"))
    return jsonify(
        _question_response(
            session,
            chosen,
            chosen.get("source", QUESTION_SOURCE_AI),
            session_state,
        )
    )


def _grade_and_store(session, answer, skipped=False, question_text=None):
    pool = _questions_for_role(session["role"])
    attempts = _session_attempts(session["id"])
    current = attempts[-1] if attempts else None
    if not current and not question_text:
        return None, (jsonify({"error": "no active question"}), 400)
    if not current:
        current = {"question_text": question_text, "question_index": int(session["current_q_index"])}
    question_text = current["question_text"]

    if not skipped and "points" in current.keys() and current["points"] is not None and current["answer"]:
        return {
            "feedback": current["feedback"] or "",
            "points": int(current["points"]),
            "breakdown": current["breakdown"] or "",
            "max_points": 3,
            "grader": current["grader"] or "rule",
            "ai_used": bool(current["ai_used"]),
            "fallback_reason": current["fallback_reason"],
            "question_number": int(current["question_index"]) + 1,
            "next_question_number": min(int(current["question_index"]) + 2, SESSION_LENGTH),
            "questions_answered": int(session["current_q_index"]),
            "session_score": sum(
                int(a["points"] or 0) for a in attempts if not a["skipped"] and a["points"] is not None
            ),
            "session_length": SESSION_LENGTH,
            "session_completed": bool(session["finalized"]),
        }, None

    meta = next((q for q in pool if q["q"] == question_text), {})
    if skipped:
        with _db() as conn:
            conn.execute(
                "UPDATE attempts SET skipped = 1, answer = NULL, grader = 'skipped', ai_used = 0, fallback_reason = NULL WHERE session_id = ? AND question_index = ?",
                (session["id"], current["question_index"]),
            )
            _advance_session(conn, session["id"], skipped=True)
        session_state = _session_progress(session["id"])
        return {
            "skipped": True,
            "question": question_text,
            "question_number": int(current["question_index"]) + 1,
            "questions_answered": session_state["questions_answered"],
            "session_length": SESSION_LENGTH,
            "session_score": session_state["session_score"],
            "session_completed": session_state["session_completed"],
        }, None

    answer = (answer or "").strip()
    if not answer:
        return None, (jsonify({"error": "answer required"}), 400)

    ai_result = ai_grade(session["role"], question_text, answer, meta)
    ai_used = bool(ai_result.get("ai_used"))
    grader = ai_result.get("grader", "ai" if ai_used else "rule")
    feedback = ai_result.get("feedback", "")
    points = int(ai_result.get("points", 0))
    breakdown = ai_result.get("breakdown", "")
    fallback_reason = ai_result.get("fallback_reason")

    with _db() as conn:
        conn.execute(
            """
            UPDATE attempts
            SET answer = ?, points = ?, feedback = ?, breakdown = ?, grader = ?, ai_used = ?, fallback_reason = ?
            WHERE session_id = ? AND question_index = ?
            """,
            (
                answer,
                points,
                feedback,
                breakdown,
                grader,
                1 if ai_used else 0,
                fallback_reason,
                session["id"],
                current["question_index"],
            ),
        )
        finalized = _advance_session(conn, session["id"])

    session_state = _session_progress(session["id"])
    result = {
        "feedback": feedback,
        "points": points,
        "breakdown": breakdown,
        "max_points": 3,
        "grader": grader,
        "ai_used": ai_used,
        "fallback_reason": fallback_reason,
        "question_number": int(current["question_index"]) + 1,
        "next_question_number": min(int(current["question_index"]) + 2, SESSION_LENGTH),
        "questions_answered": session_state["questions_answered"],
        "session_score": session_state["session_score"],
        "session_length": SESSION_LENGTH,
        "session_completed": bool(finalized),
    }
    if ai_result.get("_meta"):
        result["ai_meta"] = ai_result["_meta"]
    return result, None


@app.route("/session/submit", methods=["POST"])
def session_submit():
    session, err = _require_session()
    if err:
        return err
    if int(session["current_q_index"]) >= SESSION_LENGTH:
        _finalize_session(session["id"])
        return jsonify({"error": "session complete", "completed": True, "summary": _session_summary(session["id"])}), 409
    data = request.get_json(silent=True) or {}
    result, err = _grade_and_store(session, data.get("answer", ""))
    if err:
        return err
    return jsonify(result)


@app.route("/session/skip", methods=["POST"])
def session_skip():
    session, err = _require_session()
    if err:
        return err
    result, err = _grade_and_store(session, "", skipped=True)
    if err:
        return err
    return jsonify(result)


@app.route("/session/model-answer", methods=["POST"])
def session_model_answer():
    session, err = _require_session()
    if err:
        return err
    attempts = _session_attempts(session["id"])
    current = attempts[-1] if attempts else None
    if not current or current["skipped"] or not current["answer"]:
        return jsonify({"error": "no answered question"}), 400
    if current["model_answer"]:
        return jsonify({"model_answer": current["model_answer"], "cached": True, "ai_used": bool(current["ai_used"])})
    pool = _questions_for_role(session["role"])
    meta = next((q for q in pool if q["q"] == current["question_text"]), {})
    result = ai_model_answer(session["role"], current["question_text"], meta)
    with _db() as conn:
        conn.execute(
            "UPDATE attempts SET model_answer = ? WHERE id = ?",
            (result["model_answer"], current["id"]),
        )
    return jsonify({
        "model_answer": result["model_answer"],
        "cached": False,
        "ai_used": bool(result.get("ai_used")),
        "fallback_reason": result.get("fallback_reason"),
    })


@app.route("/correct", methods=["POST"])
def correct():
    session, err = _require_session()
    if err:
        return err
    if int(session["current_q_index"]) == 0:
        return jsonify({"error": "no active question"}), 400
    data = request.get_json(silent=True) or {}
    answer = (data.get("answer", "") or "").strip()
    if not answer:
        return jsonify({"error": "answer required"}), 400
    attempts = _session_attempts(session["id"])
    current = attempts[-1] if attempts else None
    if not current or current["skipped"]:
        return jsonify({"error": "no active question"}), 400
    pool = _questions_for_role(session["role"])
    meta = next((q for q in pool if q["q"] == current["question_text"]), {})
    existing_feedback = current["feedback"] or ""
    correction = ai_correct(session["role"], current["question_text"], answer, meta, existing_feedback)
    if not correction or "improved_answer" not in correction:
        return jsonify({"error": "correction unavailable"}), 503
    payload = {
        "improved": correction["improved_answer"],
        "changes": correction.get("changes", []),
        "explanation": correction.get("key_improvements", []),
        "ai_used": bool(correction.get("ai_used")),
        "fallback_reason": correction.get("fallback_reason"),
    }
    if correction.get("_meta"):
        payload["ai_meta"] = correction["_meta"]
    return jsonify(payload)


def rule_based_grade(role, answer, question):
    meta = next((q for q in _questions_for_role(role) if q["q"] == question), None)
    if not meta:
        return basic_grade(answer)
    answer_lower = answer.lower()
    words = answer_lower.split()
    word_count = len(words)
    ideal_length = meta.get("ideal_length", 80)
    if word_count >= ideal_length:
        length_score = 2
    elif word_count >= ideal_length * 0.6:
        length_score = 1
    else:
        length_score = 0
    keywords = meta.get("keywords", [])
    keyword_hits = [k for k in keywords if k.lower() in answer_lower]
    if len(keyword_hits) >= len(keywords) * 0.5:
        keyword_score = 2
    elif len(keyword_hits) >= len(keywords) * 0.2:
        keyword_score = 1
    else:
        keyword_score = 0
    concepts = meta.get("concepts", [])
    concept_hits = [c for c in concepts if c.lower() in answer_lower]
    if len(concept_hits) >= len(concepts) * 0.4:
        concept_score = 2
    elif len(concept_hits) >= len(concepts) * 0.2:
        concept_score = 1
    else:
        concept_score = 0
    structure_hits = sum(1 for m in ["first", "second", "third", "finally", "however", "for example", "such as", "because", "therefore"] if m in answer_lower)
    structure_score = 2 if structure_hits >= 2 else 1 if structure_hits == 1 else 0
    example_score = 2 if any(m in answer_lower for m in ["for example", "for instance", "e.g.", "such as"]) else 0
    raw = length_score * 0.2 + keyword_score * 0.3 + concept_score * 0.3 + structure_score * 0.1 + example_score * 0.1
    if raw >= 1.5:
        points = 3
    elif raw >= 1.0:
        points = 2
    elif raw >= 0.5:
        points = 1
    else:
        points = 0
    feedback = f"Rule-based grading applied. Score: {points}/3."
    breakdown = f"length={length_score}, keywords={keyword_score}, concepts={concept_score}, structure={structure_score}, examples={example_score}"
    return feedback, points, breakdown


def basic_grade(answer):
    words = len(answer.split())
    if words < 30:
        return "Rule-based grading applied. Too brief.", 0, "short answer"
    if words < 80:
        return "Rule-based grading applied. Partial answer.", 1, "partial detail"
    if words < 150:
        return "Rule-based grading applied. Reasonable answer.", 2, "good detail"
    return "Rule-based grading applied. Strong answer.", 3, "strong detail"


def _answered_attempts(user_email):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM attempts WHERE user_email = ? AND skipped = 0 AND points IS NOT NULL ORDER BY created_at ASC",
            (user_email,),
        ).fetchall()


def _user_sessions_attempts(user_email):
    with _db() as conn:
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE user_email = ? ORDER BY started_at ASC",
            (user_email,),
        ).fetchall()
        attempts = conn.execute(
            "SELECT * FROM attempts WHERE user_email = ? ORDER BY created_at ASC",
            (user_email,),
        ).fetchall()
    return sessions, attempts


def _role_averages(attempts):
    by_role = {}
    for a in attempts:
        by_role.setdefault(a["role"], []).append(int(a["points"]))
    return {role: sum(scores) / len(scores) for role, scores in by_role.items()}


def _best_role(attempts):
    averages = _role_averages(attempts)
    return max(averages, key=averages.get) if averages else "-"


def _weak_topics(attempts):
    by_topic = {}
    for a in attempts:
        topic = (a["question_topic"] or "general").strip()
        by_topic.setdefault(topic, []).append(int(a["points"]))
    rows = [
        {"topic": topic, "avg_score": round(sum(scores) / len(scores), 2), "count": len(scores)}
        for topic, scores in by_topic.items()
        if len(scores) >= 2
    ]
    rows.sort(key=lambda r: r["avg_score"])
    return rows[:5]


@app.route("/stats/unlock-status")
def stats_unlock():
    user_id, err = _check_user()
    if err:
        return err
    answered = len(_answered_attempts(user_id))
    return jsonify({"unlocked": answered >= 1, "answered": answered, "required": 1})


@app.route("/stats/summary")
def stats_summary():
    user_id, err = _check_user()
    if err:
        return err
    attempts = _answered_attempts(user_id)
    points = [int(a["points"]) for a in attempts]
    completed_dates = []
    with _db() as conn:
        sessions = conn.execute("SELECT completed_at FROM sessions WHERE user_email = ? AND completed_at IS NOT NULL", (user_id,)).fetchall()
    for row in sessions:
        completed_dates.append(row["completed_at"][:10])
    today = _utcnow().date().isoformat()
    yesterday = (_utcnow().date() - timedelta(days=1)).isoformat()
    streak = 0
    current = today if today in completed_dates else yesterday if yesterday in completed_dates else None
    if current:
        dates = set(completed_dates)
        while current in dates:
            streak += 1
            current = (datetime.fromisoformat(current).date() - timedelta(days=1)).isoformat()
    return jsonify({"total_questions": len(points), "avg_score": round(sum(points) / len(points), 1) if points else 0.0, "streak": streak, "best_role": _best_role(attempts)})


@app.route("/stats/chart-data")
def stats_chart_data():
    user_id, err = _check_user()
    if err:
        return err
    attempts = _answered_attempts(user_id)
    time_series = [
        {"index": i + 1, "date": a["created_at"][:10], "score": int(a["points"]), "role": a["role"], "question": a["question_text"][:50]}
        for i, a in enumerate(attempts)
    ]
    by_role_avg = {role: round(avg, 2) for role, avg in _role_averages(attempts).items()}
    by_difficulty = {"easy": [], "medium": [], "hard": []}
    for a in attempts:
        meta = next((q for q in _questions_for_role(a["role"]) if q["q"] == a["question_text"]), {})
        diff = _difficulty(meta.get("ideal_length", 80))
        by_difficulty[diff].append(int(a["points"]))
    by_diff_avg = {diff: round(sum(scores) / len(scores), 2) for diff, scores in by_difficulty.items() if scores}
    dist = {0: 0, 1: 0, 2: 0, 3: 0}
    for a in attempts:
        dist[int(a["points"])] += 1
    return jsonify({"time_series": time_series, "by_role": by_role_avg, "by_difficulty": by_diff_avg, "distribution": dist, "weak_topics": _weak_topics(attempts)})


@app.route("/stats/export/json")
def export_json():
    user_id, err = _check_user()
    if err:
        return err
    sessions, attempts = _user_sessions_attempts(user_id)
    payload = {
        "sessions": [dict(row) for row in sessions],
        "attempts": [dict(row) for row in attempts],
    }
    return Response(json.dumps(payload, indent=2), mimetype="application/json", headers={"Content-Disposition": f"attachment; filename=arbiethelp_history_{user_id}.json"})


@app.route("/stats/export/pdf")
def export_pdf():
    user_id, err = _check_user()
    if err:
        return err
    try:
        from fpdf import FPDF
    except ImportError:
        return jsonify({"error": "PDF generation not available"}), 503
    sessions, attempts = _user_sessions_attempts(user_id)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "ArbietHelp - Interview Report", ln=1, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"User: {user_id}", ln=1, align="C")
    pdf.cell(0, 8, f"Generated: {_utcnow().strftime('%Y-%m-%d %H:%M UTC')}", ln=1, align="C")
    pdf.ln(6)
    for i, s in enumerate(sessions, 1):
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, f"Session {i} - {s['role']}", ln=1)
        pdf.set_font("Helvetica", "", 10)
        for a in [x for x in attempts if x["session_id"] == s["id"]]:
            pdf.multi_cell(0, 5, f"Q: {a['question_text']}")
            pdf.multi_cell(0, 5, f"Score: {a['points'] if a['points'] is not None else '-'} | Grader: {a['grader'] or '-'}")
            if a["feedback"]:
                pdf.multi_cell(0, 5, f"Feedback: {a['feedback']}")
            pdf.ln(2)
    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename=arbiethelp_report_{user_id}.pdf"})


@app.route("/health")
def health():
    return jsonify(
        {
            "status": "running",
            "ai_enabled": AI_ENABLED,
            "provider": "groq",
            "model": AI_MODEL if AI_ENABLED else None,
            "prompt_version": "v1.0",
            "question_prompt_version": "v1.0_question_generator",
            "grader": "ai + rule fallback" if AI_ENABLED else "rule-based",
            "roles": list(QUESTIONS.keys()),
            "total_questions": sum(len(v) for v in QUESTIONS.values()),
        }
    )


@app.route("/roles")
def get_roles():
    return jsonify([{"name": role, "emoji": data["emoji"], "tagline": data["tagline"]} for role, data in ROLE_DATA.items()])


if __name__ == "__main__":
    print(f"ArbietHelp running on localhost:5050 | {len(QUESTIONS)} roles | {sum(len(v) for v in QUESTIONS.values())} questions")
    app.run(debug=True, port=5050)

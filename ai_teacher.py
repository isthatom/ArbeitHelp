import json
import logging
import os
import time
from pathlib import Path
from urllib import error, request as urlrequest

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)

AI_PROVIDER = "groq"
AI_ENABLED = os.getenv("AI_ENABLED", "false").lower() == "true"
AI_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "30"))
PROMPT_PATH = BASE_DIR / "prompts" / "v1.0_system.txt"
QUESTION_PROMPT_PATH = BASE_DIR / "prompts" / "v1.0_question_generator.txt"

_fail_count = 0
_circuit_open_until = 0
_threshold = 5
_cooldown = 60


def load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "You are an expert technical interviewer grading candidate answers. Return only valid JSON."


def load_question_prompt() -> str:
    if QUESTION_PROMPT_PATH.exists():
        return QUESTION_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "You generate one interview question at a time. Return only valid JSON."


def _clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return text


def _extract_text(resp) -> str:
    choices = resp.get("choices", []) if isinstance(resp, dict) else getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else getattr(choices[0], "message", None)
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, list):
        content = "".join(getattr(part, "text", "") for part in content)
    return _clean_json(content or "")


def _extract_usage(resp) -> tuple[int, int]:
    usage = resp.get("usage", {}) if isinstance(resp, dict) else getattr(resp, "usage", None)
    if not usage:
        return 0, 0
    if isinstance(usage, dict):
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    else:
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0)
    return input_tokens, output_tokens


def _fallback_grade(role, question, answer, meta):
    from app import grade

    feedback, points, breakdown = grade(role, answer, question)
    return {
        "feedback": feedback,
        "points": points,
        "breakdown": breakdown,
        "grader": "rule",
        "ai_used": False,
        "fallback_reason": "ai_unavailable",
        "_meta": {"fallback_reason": "ai_unavailable"},
    }


def _fallback_correct(role, question, answer, meta, feedback):
    improved = answer.strip()
    if improved and improved[-1] not in ".!?":
        improved += "."
    changes = []
    keywords = meta.get("keywords", [])
    for kw in keywords[:3]:
        if kw.lower() not in improved.lower():
            changes.append({"type": "add", "original": "", "improved": kw, "reason": f"Add missing keyword: {kw}"})
    if not improved:
        improved = "I would answer this by starting with the core definition, then explain trade-offs, and end with a concrete example."
        changes.append({"type": "add", "original": "", "improved": improved, "reason": "Provide a usable baseline answer"})
    return {
        "improved_answer": improved,
        "changes": changes[:6],
        "key_improvements": [c["reason"] for c in changes[:4]] or ["Rule-based correction applied"],
        "ai_used": False,
        "fallback_reason": "ai_unavailable",
        "_meta": {"fallback_reason": "ai_unavailable"},
    }


def _invoke_ai(messages, system_prompt, expected_tokens_out=900):
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "temperature": 0.2,
        "max_completion_tokens": expected_tokens_out,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
    }
    req = urlrequest.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ArbeitHelp/1.0",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=AI_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"groq_http_error:{exc.code}:{detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"groq_network_error:{exc.reason}") from exc


def _build_grade_prompt(role, question, answer, meta):
    payload = {
        "role": role,
        "question": question,
        "candidate_answer": answer,
        "question_metadata": {
            "keywords": meta.get("keywords", []),
            "concepts": meta.get("concepts", []),
            "common_mistakes": meta.get("common_mistakes", []),
            "ideal_length": meta.get("ideal_length", 80),
        },
        "grading_rules": {
            "points_range": [0, 3],
            "output_format": {"feedback": "string", "points": 0, "breakdown": "string"},
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _build_correct_prompt(role, question, answer, meta, feedback):
    payload = {
        "role": role,
        "question": question,
        "candidate_answer": answer,
        "feedback": feedback,
        "question_metadata": {
            "keywords": meta.get("keywords", []),
            "concepts": meta.get("concepts", []),
            "common_mistakes": meta.get("common_mistakes", []),
            "ideal_length": meta.get("ideal_length", 80),
        },
        "output_format": {
            "improved_answer": "string",
            "changes": [{"type": "add|replace|remove", "original": "string", "improved": "string", "reason": "string"}],
            "key_improvements": ["string"],
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _model_supported_or_fail():
    if not AI_MODEL:
        logger.warning("No Groq model configured")
        return False
    return True


def _build_question_prompt(role, question_number, session_length, session_context):
    payload = {
        "role": role,
        "question_number": question_number,
        "session_length": session_length,
        "session_context": session_context,
        "output_format": {
            "question": "string",
            "topic": "string",
            "difficulty": "easy|medium|hard",
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def ai_generate_question(role, question_number, session_context, session_length=5):
    if not AI_ENABLED or not os.getenv("GROQ_API_KEY") or not _model_supported_or_fail():
        return None

    try:
        start = time.time()
        resp = _invoke_ai(
            [{"role": "user", "content": _build_question_prompt(role, question_number, session_length, session_context)}],
            load_question_prompt(),
            expected_tokens_out=600,
        )
        latency_ms = int((time.time() - start) * 1000)
        text = _extract_text(resp)
        result = json.loads(text)
        question = str(result.get("question", "")).strip()
        topic = str(result.get("topic", "")).strip()
        difficulty = str(result.get("difficulty", "")).strip().lower()
        if not question or not topic or difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("invalid question generation response")

        tokens_in, tokens_out = _extract_usage(resp)
        return {
            "question": question,
            "topic": topic,
            "difficulty": difficulty,
            "ai_used": True,
            "fallback_reason": None,
            "_meta": {
                "provider": AI_PROVIDER,
                "model": AI_MODEL,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
            },
        }
    except Exception as exc:
        logger.exception("AI question generation failed: %s", exc)
        return None


def ai_grade(role, question, answer, meta):
    global _fail_count, _circuit_open_until
    if not AI_ENABLED or not os.getenv("GROQ_API_KEY") or not _model_supported_or_fail():
        return _fallback_grade(role, question, answer, meta)

    if _circuit_open_until > time.time() or _fail_count >= _threshold:
        _circuit_open_until = time.time() + _cooldown
        return _fallback_grade(role, question, answer, meta)

    try:
        start = time.time()
        resp = _invoke_ai([{"role": "user", "content": _build_grade_prompt(role, question, answer, meta)}], load_system_prompt())
        latency_ms = int((time.time() - start) * 1000)
        text = _extract_text(resp)
        result = json.loads(text)
        points = int(result.get("points"))
        if points not in (0, 1, 2, 3):
            raise ValueError("invalid points")

        tokens_in, tokens_out = _extract_usage(resp)
        _fail_count = 0
        _circuit_open_until = 0
        return {
            "feedback": str(result.get("feedback", "")).strip(),
            "points": points,
            "breakdown": str(result.get("breakdown", "")).strip(),
            "grader": "ai",
            "ai_used": True,
            "fallback_reason": None,
            "_meta": {
                "provider": AI_PROVIDER,
                "model": AI_MODEL,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
            },
        }
    except Exception as exc:
        _fail_count += 1
        logger.exception("AI grading failed: %s", exc)
        return _fallback_grade(role, question, answer, meta) | {"fallback_reason": "ai_error"}


def ai_correct(role, question, answer, meta, feedback):
    global _fail_count, _circuit_open_until
    if not AI_ENABLED or not os.getenv("GROQ_API_KEY") or not _model_supported_or_fail():
        return _fallback_correct(role, question, answer, meta, feedback)

    if _circuit_open_until > time.time() or _fail_count >= _threshold:
        _circuit_open_until = time.time() + _cooldown
        return _fallback_correct(role, question, answer, meta, feedback)

    try:
        start = time.time()
        resp = _invoke_ai([{"role": "user", "content": _build_correct_prompt(role, question, answer, meta, feedback)}], "You improve interview answers. Return only valid JSON.", expected_tokens_out=1100)
        latency_ms = int((time.time() - start) * 1000)
        text = _extract_text(resp)
        result = json.loads(text)
        if not isinstance(result.get("changes"), list) or "improved_answer" not in result or "key_improvements" not in result:
            raise ValueError("invalid correction response")

        tokens_in, tokens_out = _extract_usage(resp)
        _fail_count = 0
        _circuit_open_until = 0
        return {
            "improved_answer": str(result.get("improved_answer", "")).strip(),
            "changes": result.get("changes", []),
            "key_improvements": result.get("key_improvements", []),
            "ai_used": True,
            "fallback_reason": None,
            "_meta": {
                "provider": AI_PROVIDER,
                "model": AI_MODEL,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
            },
        }
    except Exception as exc:
        _fail_count += 1
        logger.exception("AI correction failed: %s", exc)
        return _fallback_correct(role, question, answer, meta, feedback) | {"fallback_reason": "ai_error"}

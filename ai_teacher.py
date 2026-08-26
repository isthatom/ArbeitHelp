import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from urllib import error, request as urlrequest

from config import (
    AI_ENABLED,
    AI_MODEL,
    AI_PROVIDER,
    AI_TIMEOUT,
    HINT_PROMPT_PATH,
    JD_PROMPT_PATH,
    PROMPT_PATH,
    QUESTION_PROMPT_PATH,
)

logger = logging.getLogger(__name__)

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


def load_hint_prompt() -> str:
    if HINT_PROMPT_PATH.exists():
        return HINT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "You give one short hint for an interview question. Return only valid JSON."


def load_jd_prompt() -> str:
    if JD_PROMPT_PATH.exists():
        return JD_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "You extract structured skills from a job description. Return only valid JSON."


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
    from app import rule_based_grade

    feedback, points, breakdown = rule_based_grade(role, answer, question)
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


_executor = ThreadPoolExecutor(max_workers=4)


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

    def _send():
        with urlrequest.urlopen(req, timeout=AI_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        future = _executor.submit(_send)
        return future.result(timeout=AI_TIMEOUT)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"groq_http_error:{exc.code}:{detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"groq_network_error:{exc.reason}") from exc
    except FutureTimeoutError as exc:
        raise RuntimeError(f"groq_timeout:{AI_TIMEOUT}s exceeded") from exc


def _question_metadata(meta):
    return {
        "keywords": meta.get("keywords", []),
        "concepts": meta.get("concepts", []),
        "common_mistakes": meta.get("common_mistakes", []),
        "ideal_length": meta.get("ideal_length", 80),
    }


def _build_grade_prompt(role, question, answer, meta):
    payload = {
        "role": role,
        "question": question,
        "candidate_answer": answer,
        "question_metadata": _question_metadata(meta),
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
        "question_metadata": _question_metadata(meta),
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
            "question_style": "scenario|conceptual",
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def ai_generate_question(role, question_number, session_context, session_length=5):
    global _fail_count, _circuit_open_until
    if not AI_ENABLED or not os.getenv("GROQ_API_KEY") or not _model_supported_or_fail():
        return None

    if _circuit_open_until > time.time() or _fail_count >= _threshold:
        _circuit_open_until = time.time() + _cooldown
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
        question_style = str(result.get("question_style", "")).strip().lower()
        if question_style not in {"scenario", "conceptual"}:
            question_style = "conceptual"
        if not question or not topic or difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("invalid question generation response")

        tokens_in, tokens_out = _extract_usage(resp)
        _fail_count = 0
        _circuit_open_until = 0
        return {
            "question": question,
            "topic": topic,
            "difficulty": difficulty,
            "question_style": question_style,
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
        logger.exception("AI question generation failed: %s", exc)
        return None


def _build_model_answer_prompt(role, question, meta):
    payload = {
        "role": role,
        "question": question,
        "question_metadata": _question_metadata(meta),
        "output_format": {"model_answer": "string"},
    }
    return json.dumps(payload, ensure_ascii=False)


def _fallback_model_answer(role, question, meta):
    parts = ["A strong answer starts with a clear definition of the core idea."]
    keywords = meta.get("keywords", [])
    if keywords:
        parts.append("Cover key terms: " + ", ".join(keywords[:4]) + ".")
    concepts = meta.get("concepts", [])
    if concepts:
        parts.append("Explain the core concepts: " + ", ".join(concepts[:4]) + ".")
    parts.append("Close with a concrete example from your experience or a real-world scenario.")
    return " ".join(parts)


def ai_model_answer(role, question, meta):
    global _fail_count, _circuit_open_until
    if not AI_ENABLED or not os.getenv("GROQ_API_KEY") or not _model_supported_or_fail():
        return {"model_answer": _fallback_model_answer(role, question, meta), "ai_used": False, "fallback_reason": "ai_unavailable"}

    if _circuit_open_until > time.time() or _fail_count >= _threshold:
        _circuit_open_until = time.time() + _cooldown
        return {"model_answer": _fallback_model_answer(role, question, meta), "ai_used": False, "fallback_reason": "ai_unavailable"}

    try:
        start = time.time()
        resp = _invoke_ai([{"role": "user", "content": _build_model_answer_prompt(role, question, meta)}], load_system_prompt(), expected_tokens_out=900)
        latency_ms = int((time.time() - start) * 1000)
        text = _extract_text(resp)
        result = json.loads(text)
        model_answer = str(result.get("model_answer", "")).strip()
        if not model_answer:
            raise ValueError("invalid model answer response")

        tokens_in, tokens_out = _extract_usage(resp)
        _fail_count = 0
        _circuit_open_until = 0
        return {
            "model_answer": model_answer,
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
        logger.exception("AI model answer failed: %s", exc)
        return {"model_answer": _fallback_model_answer(role, question, meta), "ai_used": False, "fallback_reason": "ai_error"}


def _fallback_hint(meta):
    concepts = [str(c).strip() for c in (meta.get("concepts") or []) if c]
    keywords = [str(k).strip() for k in (meta.get("keywords") or []) if k]
    if concepts:
        return f"Consider mentioning: {concepts[0]}."
    if keywords:
        return f"Consider mentioning: {keywords[0]}."
    return "Think of a concrete example from real-world practice."


def _build_hint_prompt(role, question, meta):
    payload = {
        "role": role,
        "question": question,
        "question_metadata": _question_metadata(meta),
        "output_format": {"hint": "string"},
    }
    return json.dumps(payload, ensure_ascii=False)


def ai_hint(role, question, meta):
    global _fail_count, _circuit_open_until
    if not AI_ENABLED or not os.getenv("GROQ_API_KEY") or not _model_supported_or_fail():
        return {"hint": _fallback_hint(meta), "ai_used": False, "fallback_reason": "ai_unavailable"}

    if _circuit_open_until > time.time() or _fail_count >= _threshold:
        _circuit_open_until = time.time() + _cooldown
        return {"hint": _fallback_hint(meta), "ai_used": False, "fallback_reason": "ai_unavailable"}

    try:
        start = time.time()
        resp = _invoke_ai(
            [{"role": "user", "content": _build_hint_prompt(role, question, meta)}],
            load_hint_prompt(),
            expected_tokens_out=100,
        )
        latency_ms = int((time.time() - start) * 1000)
        text = _extract_text(resp)
        result = json.loads(text)
        hint = str(result.get("hint", "")).strip()
        if not hint:
            raise ValueError("invalid hint response")

        tokens_in, tokens_out = _extract_usage(resp)
        _fail_count = 0
        _circuit_open_until = 0
        return {
            "hint": hint,
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
        logger.exception("AI hint failed: %s", exc)
        return {"hint": _fallback_hint(meta), "ai_used": False, "fallback_reason": "ai_error"}


def _empty_jd():
    return {"skills": [], "tools": [], "seniority": "", "focus_areas": []}


def _jd_string_list(value, cap=12):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip().lower()
        if text and text not in out:
            out.append(text)
        if len(out) >= cap:
            break
    return out


def _validate_jd(result):
    seniority = str(result.get("seniority", "")).strip().lower()[:40]
    return {
        "skills": _jd_string_list(result.get("skills")),
        "tools": _jd_string_list(result.get("tools")),
        "seniority": seniority,
        "focus_areas": _jd_string_list(result.get("focus_areas")),
    }


def _build_jd_prompt(jd_text):
    payload = {
        "job_description": jd_text,
        "output_format": _empty_jd(),
    }
    return json.dumps(payload, ensure_ascii=False)


def ai_extract_jd(jd_text):
    global _fail_count, _circuit_open_until
    if not AI_ENABLED or not os.getenv("GROQ_API_KEY") or not _model_supported_or_fail():
        return {**_empty_jd(), "ai_used": False, "fallback_reason": "ai_unavailable"}

    if _circuit_open_until > time.time() or _fail_count >= _threshold:
        _circuit_open_until = time.time() + _cooldown
        return {**_empty_jd(), "ai_used": False, "fallback_reason": "ai_unavailable"}

    try:
        start = time.time()
        resp = _invoke_ai(
            [{"role": "user", "content": _build_jd_prompt(jd_text)}],
            load_jd_prompt(),
            expected_tokens_out=400,
        )
        latency_ms = int((time.time() - start) * 1000)
        text = _extract_text(resp)
        result = json.loads(text)
        validated = _validate_jd(result)
        if not (validated["skills"] or validated["tools"] or validated["focus_areas"] or validated["seniority"]):
            raise ValueError("empty JD extraction response")

        tokens_in, tokens_out = _extract_usage(resp)
        _fail_count = 0
        _circuit_open_until = 0
        return {
            **validated,
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
        logger.exception("AI JD extraction failed: %s", exc)
        return {**_empty_jd(), "ai_used": False, "fallback_reason": "ai_error"}


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

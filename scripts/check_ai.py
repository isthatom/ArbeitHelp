"""Check the hosted Groq connection without printing the API key."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import AI_ENABLED, AI_MODEL  # noqa: E402
from ai_teacher import _invoke_ai  # noqa: E402


def main():
    key = os.getenv("GROQ_API_KEY", "").strip()
    print(f"AI enabled: {AI_ENABLED}; model: {AI_MODEL}; key configured: {bool(key)}")
    if not AI_ENABLED or not key:
        print("Set AI_ENABLED=true and GROQ_API_KEY in the project's hosted .env, then reload the web app.")
        return 1
    try:
        response = _invoke_ai(
            [{"role": "user", "content": 'Return a JSON object with "status" set to "ok".'}],
            "Return only valid JSON.",
            expected_tokens_out=900,
        )
        content = json.loads(response["choices"][0]["message"]["content"])
        if content.get("status") != "ok":
            print("Groq responded, but its JSON did not pass the connection check.")
            return 1
    except Exception as exc:
        detail = str(exc).replace(key, "[redacted]")
        print(f"Groq connection failed: {detail[:1500]}")
        if "groq_http_error:401" in detail:
            print("Replace the hosted GROQ_API_KEY with a valid key from your Groq Console.")
        elif "groq_http_error:429" in detail:
            print("Free-tier quota reached. Wait for the limit to reset; fallback interviews still work.")
        elif "groq_network_error:" in detail or "groq_http_error:403" in detail:
            print("Check PythonAnywhere outbound/proxy access to api.groq.com and the provider error above.")
        elif "groq_timeout:" in detail:
            print("Groq did not respond within AI_TIMEOUT. Retry later; fallback interviews still work.")
        return 1
    print("Groq request passed using the app's actual request code.")
    print("Reload the Web app and start a new interview to clear any earlier circuit-breaker state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

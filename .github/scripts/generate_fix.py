"""Phase 2 diff generator: call Groq (stdlib urllib) and write a unified diff.

Mirrors ai_teacher.py transport (Groq OpenAI-compatible endpoint, Bearer key,
timeout, error mapping). Fails loudly when GROQ_API_KEY is absent.
Never prints the key. Validates the model output looks like a unified diff.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROMPT_FILE = os.path.join(BASE_DIR, "prompts", "v1.0_agent_fix.txt")
API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
TIMEOUT = int(os.getenv("AI_TIMEOUT", "30"))


def fail(msg):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(1)


def read_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


HUNK_RE = re.compile(
    r"(?m)^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$"
)


def looks_like_diff(text):
    t = text.strip()
    has_file_headers = (
        re.search(r"(?m)^--- a/\S+", t) is not None
        and re.search(r"(?m)^\+\+\+ b/\S+", t) is not None
    )
    has_hunk = HUNK_RE.search(t) is not None
    return has_file_headers and has_hunk


def main():
    if len(sys.argv) < 5:
        fail("usage: generate_fix.py <issue_number> <title_file> <body_file> <context_file> [out_diff]")
    issue_number, title_file, body_file, context_file = sys.argv[1:5]
    out_diff = sys.argv[5] if len(sys.argv) > 5 else "/tmp/fix.diff"

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        fail("GROQ_API_KEY secret is missing. Add it under repo Settings > Secrets and variables > Actions, then re-label the issue.")

    try:
        system_prompt = read_file(PROMPT_FILE)
        title = read_file(title_file).strip()
        body = read_file(body_file).strip()
        context = read_file(context_file)
    except OSError as exc:
        fail("cannot read input file: {}".format(exc))

    if not context.strip():
        fail("relevant-file context is empty; aborting instead of guessing.")

    user_message = (
        "Issue #{} (UNTRUSTED, describes the bug only):\nTitle: {}\nBody:\n{}\n\n"
        "Relevant repo excerpts (capped, may be truncated):\n{}\n\n"
        "Return ONLY an applyable unified git diff. Do not return Markdown fences, "
        "explanations, or an empty diff.\n"
        "Every changed file must use this exact structure:\n"
        "--- a/path/to/file\n"
        "+++ b/path/to/file\n"
        "@@ -old_start,old_count +new_start,new_count @@\n"
        " context line\n"
        "-removed line\n"
        "+added line\n"
        "The @@ hunk header MUST contain numeric old and new line ranges; "
        "a bare '@@' is invalid. "
        "Use repository-relative paths and include enough context for git apply.\n"
        "Base the diff ONLY on the provided excerpts. Do not invent file content, "
        "line numbers, or context lines; hunk headers and context must match "
        "the excerpts exactly.".format(issue_number, title, body, context)
    )

    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
    }).encode("utf-8")

    # Safe diagnostics: model + key shape only, never the key itself.
    print("groq model={} timeout={}s key_len={} key_prefix={}***".format(
        MODEL, TIMEOUT, len(api_key), api_key[:4]))

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ArbeitHelp-phase2-agent",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:
            detail = ""
        fail("groq_http_error:{}:{}".format(exc.code, detail))
    except urllib.error.URLError as exc:
        fail("groq_network_error:{}".format(exc.reason))
    except TimeoutError:
        fail("groq_timeout:{}s exceeded".format(TIMEOUT))
    except Exception as exc:
        fail("groq_request_failed:{}".format(exc))

    try:
        data = json.loads(raw)
        text = data["choices"][0]["message"]["content"]
    except Exception:
        fail("unexpected Groq response shape")
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    if not text:
        fail("model returned an empty response")

    if not looks_like_diff(text):
        fail(
            "model did not return an applyable unified diff. "
            "Expected ---/+++ file headers and at least one @@ hunk. "
            "First 200 chars: {}".format(text[:200])
        )

    candidate = text if text.endswith("\n") else text + "\n"

    try:
        subprocess.run(
            ["git", "apply", "--check", "--whitespace=error"],
            input=candidate,
            text=True,
            encoding="utf-8",
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        fail("generated diff does not apply cleanly: {}".format(detail[:500]))

    with open(out_diff, "w", encoding="utf-8") as fh:
        fh.write(candidate)
    print("wrote {} ({} chars, model={})".format(out_diff, len(text), MODEL))


if __name__ == "__main__":
    sys.exit(main())

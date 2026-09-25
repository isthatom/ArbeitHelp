"""Phase 2 relevance picker: map issue text to a capped set of repo files.

Reads ISSUE_TITLE / ISSUE_BODY from env (or --title/--body), scores a
keyword -> files map, writes combined context to --out (default /tmp/context.txt),
and appends RELEVANT_FILES to $GITHUB_OUTPUT when present. Stdlib only.
"""
import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
MAX_TOTAL_CHARS = 12_000

# (keywords, files) — ordered by specificity; keep small, never whole repo.
RULES = [
    (("route", "session", "auth", "login", "signup", "jwt", "api", "submit", "skip", "recap", "grade", "grading"), ["app.py"]),
    (("groq", "prompt", "hint", "model answer", "improve", "timeout", "fallback", "grading"), ["ai_teacher.py", "prompts/v1.0_system.txt", "prompts/v1.0_question_generator.txt", "prompts/v1.0_hint.txt", "prompts/v1.0_jd_parser.txt"]),
    (("config", "env", "model", "timeout", "path"), ["config.py"]),
    (("question bank", "questions.json", "role", "difficulty"), ["data/questions.json"]),
    (("button", "page", "css", "frontend", "modal", "timer", "chart", "html", "js"), ["static/common.js", "static/script.js", "static/questions.js", "static/stats.js", "static/index.html", "static/questions.html", "static/stats.html"]),
    (("test", "pytest", "route test", "contract"), ["tests/test_routes.py", "tests/conftest.py"]),
]

FALLBACK_FILES = ["app.py", "config.py"]


def score_files(text):
    text = text.lower()
    scores = {}
    for keywords, files in RULES:
        hits = sum(1 for k in keywords if k in text)
        if hits:
            for f in files:
                scores[f] = scores.get(f, 0) + hits
    if not scores:
        return list(FALLBACK_FILES)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [f for f, _ in ranked]


def build_context(files):
    parts = []
    total = 0
    used = []
    for rel in files:
        p = BASE / rel
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        # Per-file cap so one big file (app.py) can't eat the budget.
        if len(content) > 8_000:
            content = content[:8_000] + "\n... [truncated]\n"
        if total + len(content) > MAX_TOTAL_CHARS:
            remaining = MAX_TOTAL_CHARS - total
            if remaining > 500:
                parts.append("===== FILE: {} (truncated) =====\n".format(rel) + content[:remaining])
                used.append(rel)
            break
        parts.append("===== FILE: {} =====\n".format(rel) + content)
        used.append(rel)
        total += len(content)
        if total >= MAX_TOTAL_CHARS:
            break
    return used, "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default=os.getenv("ISSUE_TITLE", ""))
    ap.add_argument("--body", default=os.getenv("ISSUE_BODY", ""))
    ap.add_argument("--out", default="/tmp/context.txt")
    args = ap.parse_args()

    ranked = score_files(args.title + "\n" + args.body)
    used, context = build_context(ranked)
    Path(args.out).write_text(context, encoding="utf-8")

    line = "RELEVANT_FILES=" + ",".join(used)
    print(line)
    print("context_chars={} out={}".format(len(context), args.out))
    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())

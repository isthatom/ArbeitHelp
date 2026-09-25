"""Phase 2 relevance picker: map issue text to a capped set of repo files.

Reads ISSUE_TITLE / ISSUE_BODY from env (or --title/--body), scores a
keyword -> files map with an exact-filename boost for files named in the
issue text, writes combined context to --out (default /tmp/context.txt),
and appends RELEVANT_FILES to $GITHUB_OUTPUT when present. Stdlib only.
"""
import argparse
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
MAX_TOTAL_CHARS = 20_000
PER_FILE_CHARS = 6_000

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


def known_files():
    seen = []
    for _, files in RULES:
        for f in files:
            if f not in seen:
                seen.append(f)
    return seen


def filename_hits(text):
    """Repo files explicitly named in the issue text, most-specific first.

    Matches full relative paths (static/questions.js) or bare basenames
    (questions.js) as standalone tokens. These outrank keyword scores so a
    file named in the title can never be starved by the char budget.
    """
    lowered = text.lower()
    hits = []
    for rel in known_files():
        base = rel.rsplit("/", 1)[-1].lower()
        if rel.lower() in lowered:
            hits.append(rel)
        elif re.search(r"(?<![\w./-])" + re.escape(base) + r"(?![\w.-])", lowered):
            hits.append(rel)
    return sorted(set(hits), key=lambda r: (-len(r), r))


def score_files(text):
    lowered = text.lower()
    scores = {}
    for keywords, files in RULES:
        hits = sum(1 for k in keywords if k in lowered)
        if hits:
            for f in files:
                scores[f] = scores.get(f, 0) + hits
    ranked_kw = [f for f, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
    named = filename_hits(text)
    if not ranked_kw and not named:
        return list(FALLBACK_FILES)
    named_set = set(named)
    return named + [f for f in ranked_kw if f not in named_set]


def build_context(files):
    parts = []
    total = 0
    used = []
    skipped = []
    existing = []
    for rel in files:
        p = BASE / rel
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        existing.append((rel, size))
    for i, (rel, size) in enumerate(existing):
        try:
            content = (BASE / rel).read_text(encoding="utf-8")
        except OSError:
            skipped.append("{} (unreadable)".format(rel))
            continue
        # Per-file cap so one big file (app.py) can't eat the budget.
        if len(content) > PER_FILE_CHARS:
            content = content[:PER_FILE_CHARS] + "\n... [truncated]\n"
        if total + len(content) > MAX_TOTAL_CHARS:
            remaining = MAX_TOTAL_CHARS - total
            if remaining > 500:
                parts.append("===== FILE: {} (truncated) =====\n".format(rel) + content[:remaining])
                used.append(rel)
                total += remaining
            else:
                skipped.append("{} ({} chars, budget exhausted)".format(rel, size))
            for rel2, size2 in existing[i + 1:]:
                skipped.append("{} ({} chars, budget exhausted)".format(rel2, size2))
            break
        parts.append("===== FILE: {} =====\n".format(rel) + content)
        used.append(rel)
        total += len(content)
        if total >= MAX_TOTAL_CHARS:
            for rel2, size2 in existing[i + 1:]:
                skipped.append("{} ({} chars, budget exhausted)".format(rel2, size2))
            break
    return used, skipped, "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default=os.getenv("ISSUE_TITLE", ""))
    ap.add_argument("--body", default=os.getenv("ISSUE_BODY", ""))
    ap.add_argument("--out", default="/tmp/context.txt")
    args = ap.parse_args()

    ranked = score_files(args.title + "\n" + args.body)
    used, skipped, context = build_context(ranked)
    Path(args.out).write_text(context, encoding="utf-8")

    line = "RELEVANT_FILES=" + ",".join(used)
    print(line, flush=True)
    print("context_chars={} files_used={} out={}".format(len(context), len(used), args.out), flush=True)
    if skipped:
        print("SKIPPED_FILES=" + ";".join(skipped), flush=True)
    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())

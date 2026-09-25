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

WINDOW_LINES = 30
MAX_WINDOWS_PER_FILE = 3

# Vacuous English/template words: useless as code-search terms.
STOPWORDS = frozenset(
    "the and for with from that this these those after before when then than "
    "into over under while lines around handling element expected relevant repro "
    "text first keep visible open start click user stays enabled fetched fetch "
    "shows label resets successfully session static".split()
)


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


def extract_terms(title, body):
    """Code-ish search terms from the issue: `code spans` first, then tokens."""
    text = title + "\n" + body
    terms = set()

    def add_token(tok):
        tok = tok.strip(".,;:!?()[]{}\"'").lower()
        if len(tok) >= 3:
            terms.add(tok)

    for m in re.findall(r"`([^`]+)`", text):
        for tok in re.split(r"\s+", m.strip()):
            add_token(tok)
    for m in re.findall(r'"([^"]+)"', text):
        for tok in re.split(r"\s+", m.strip()):
            add_token(tok)
    for tok in re.findall(r"[A-Za-z][\w.-]*", text):
        t = tok.strip(".,;:!?()[]{}\"'").lower()
        if len(t) < 4 or t in STOPWORDS:
            continue
        if t.endswith((".js", ".html", ".css", ".py", ".json", ".txt")):
            continue  # filenames select files (filename_hits), not lines
        terms.add(t)
    return terms


def excerpt_regions(lines, terms, max_windows=MAX_WINDOWS_PER_FILE):
    """(start, end) spans covering the file's regions, densest first per region.

    The file is split into `max_windows` segments and the most term-dense
    matching line is taken per segment. Pure density ranking clusters every
    window where the vocabulary is densest (usually the top of the file) and
    can miss the buggy function 600 lines down; spreading guarantees the
    head, middle, and tail each get a chance.
    """
    scored = []
    for i, line in enumerate(lines):
        ll = line.lower()
        hits = sum(1 for t in terms if t in ll)
        if hits:
            scored.append((hits, i))
    if not scored:
        return []
    scored.sort(key=lambda x: (-x[0], x[1]))
    spans = []
    taken = [False] * len(lines)
    seg = len(lines) / max_windows
    for k in range(max_windows):
        lo = int(k * seg)
        hi = int((k + 1) * seg) - 1 if k < max_windows - 1 else len(lines) - 1
        for hits, i in scored:
            if lo <= i <= hi:
                s = max(0, i - WINDOW_LINES)
                e = min(len(lines) - 1, i + WINDOW_LINES)
                if any(taken[s:e + 1]):
                    continue
                spans.append((s, e))
                for j in range(s, e + 1):
                    taken[j] = True
                break
    return sorted(spans)


def excerpt_file(rel, content, terms):
    """Region excerpts with true line numbers, or head truncation as fallback."""
    lines = content.splitlines()
    spans = excerpt_regions(lines, terms)
    if not spans:
        head = content[:PER_FILE_CHARS] + "\n... [truncated]\n"
        return "===== FILE: {} (head, no term match) =====\n".format(rel) + head
    chunks = []
    for s, e in spans:
        chunks.append(
            "===== FILE: {} (lines {}-{} of {}) =====\n".format(rel, s + 1, e + 1, len(lines))
            + "\n".join(lines[s:e + 1])
        )
    return "\n... [lines omitted] ...\n".join(chunks)


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


def build_context(files, terms):
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
        # Oversized files contribute term-anchored regions with true line
        # numbers instead of a blind head slice, so the model sees the code
        # the issue is actually about.
        if len(content) > PER_FILE_CHARS:
            content = excerpt_file(rel, content, terms)
        else:
            content = "===== FILE: {} =====\n".format(rel) + content
        if total + len(content) > MAX_TOTAL_CHARS:
            remaining = MAX_TOTAL_CHARS - total
            if remaining > 500:
                # content already carries its FILE header(s); slice only.
                parts.append(content[:remaining] + "\n... [cut: total budget] ...\n")
                used.append(rel)
                total += remaining
            else:
                skipped.append("{} ({} chars, budget exhausted)".format(rel, size))
            for rel2, size2 in existing[i + 1:]:
                skipped.append("{} ({} chars, budget exhausted)".format(rel2, size2))
            break
        parts.append(content)
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
    terms = sorted(extract_terms(args.title, args.body))
    used, skipped, context = build_context(ranked, terms)
    Path(args.out).write_text(context, encoding="utf-8")

    line = "RELEVANT_FILES=" + ",".join(used)
    print(line, flush=True)
    print("context_chars={} files_used={} out={}".format(len(context), len(used), args.out), flush=True)
    print("SEARCH_TERMS=" + ",".join(terms[:20]), flush=True)
    if skipped:
        print("SKIPPED_FILES=" + ";".join(skipped), flush=True)
    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())

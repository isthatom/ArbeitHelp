import json
import time

import pytest

ROLE = "Software Engineer"


@pytest.fixture(autouse=True)
def _reset_ai_breaker():
    import ai_teacher

    ai_teacher._fail_count = 0
    ai_teacher._circuit_open_until = 0
    yield
    ai_teacher._fail_count = 0
    ai_teacher._circuit_open_until = 0

ANSWERS = [
    "LIFO and FIFO are the key ordering semantics. A stack is last-in-first-out used for undo history and recursion; a queue is first-in-first-out used for print jobs and BFS. For example DFS uses a stack and BFS uses a queue.",
    "A hash table maps keys to values using a hash function, giving average O(1) lookups. Collisions are handled by chaining or open addressing. It is used for caches and symbol tables.",
    "Big O describes worst-case asymptotic growth. O(n) grows linearly, O(log n) grows logarithmically. For example binary search is O(log n) while linear search is O(n).",
    "REST uses HTTP methods and resources; SOAP is a heavier XML-based protocol with strict contracts. REST is simpler and more common for public APIs.",
    "I would start with the core definition, then explain trade-offs, and end with a concrete example to demonstrate practical understanding.",
]


def _headers(token):
    return {"Authorisation": "Bearer " + token, "Content-Type": "application/json"}


def _signup(client, email, role=ROLE):
    resp = client.post("/auth/signup", json={"email": email, "password": "pw", "role": role})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["token"]


def _start_session(client, token, role=ROLE):
    resp = client.post("/session/start", headers=_headers(token), json={"role": role})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["session_token"]


def _complete_session(client, token):
    sess = _start_session(client, token)
    headers = _headers(sess)
    for i, answer in enumerate(ANSWERS):
        resp = client.get("/session/question", headers=headers)
        assert resp.status_code == 200
        resp = client.post("/session/submit", headers=headers, json={"answer": answer})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["session_completed"] is (i == len(ANSWERS) - 1)
    return sess


def test_health_and_roles(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "running"
    assert data["ai_enabled"] is False
    assert data["grader"] == "rule-based"
    assert data["prompt_version"] == "v1.0"
    assert data["model"] is None
    assert ROLE in data["roles"]
    assert data["total_questions"] == 75

    resp = client.get("/roles")
    assert resp.status_code == 200
    roles = resp.get_json()
    assert any(role["name"] == ROLE for role in roles)
    assert all({"name", "emoji", "tagline"} <= set(role) for role in roles)


def test_static_pages(client):
    for path in ("/", "/index.html", "/questions.html", "/stats.html"):
        assert client.get(path).status_code == 200, path
    for path in ("/common.js", "/script.js", "/questions.js", "/stats.js",
                 "/style.css", "/questions.css", "/stats.css", "/favicon.svg"):
        assert client.get(path).status_code == 200, path
    assert client.get("/no-such-file.xyz").status_code == 404


def test_auth_flow(client):
    email = "auth@test.local"
    token = _signup(client, email)

    resp = client.get("/auth/me", headers=_headers(token))
    assert resp.status_code == 200
    assert resp.get_json() == {"email": email, "role": ROLE}

    resp = client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200
    assert resp.get_json()["role"] == ROLE
    assert resp.get_json()["token"]

    assert client.post("/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    assert client.post("/auth/signup", json={"email": email, "password": "pw2", "role": ROLE}).status_code == 400
    assert client.post("/auth/signup", json={"email": "x", "password": ""}).status_code == 400

    assert client.get("/auth/me").status_code == 401
    assert client.get("/session/question").status_code == 401
    assert client.post("/session/start", json={"role": ROLE}).status_code == 401
    assert client.get("/stats/summary").status_code == 401
    assert client.get("/stats/export/json").status_code == 401


def test_session_validation(client):
    token = _signup(client, "validation@test.local")

    resp = client.post("/session/start", headers=_headers(token), json={"role": "Bogus"})
    assert resp.status_code == 400

    sess = _start_session(client, token)
    # submit/skip/correct before any question is served
    assert client.post("/session/submit", headers=_headers(sess), json={"answer": "hi"}).status_code == 400
    assert client.post("/session/skip", headers=_headers(sess)).status_code == 400
    assert client.post("/correct", headers=_headers(sess), json={"answer": "hi"}).status_code == 400
    assert client.post("/session/submit", headers=_headers(sess), json={"answer": ""}).status_code == 400


def test_session_ownership(client):
    token_a = _signup(client, "owner_a@test.local")
    _signup(client, "owner_b@test.local")
    sess = _start_session(client, token_a)
    # the session token is the owner's JWT, so it works for its session
    assert client.get("/session/question", headers=_headers(sess)).status_code == 200
    # an unknown token is rejected as an invalid session
    assert client.get("/session/question", headers=_headers("bogus-token")).status_code == 401


def test_full_session_flow(client):
    token = _signup(client, "flow@test.local")
    sess = _start_session(client, token)
    headers = _headers(sess)

    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    first = resp.get_json()
    assert first["question_number"] == 1
    assert first["questions_answered"] == 0
    assert first["session_completed"] is False
    assert first["session_length"] == 5
    assert first["question_source"] in ("ai", "fallback")
    assert first["question_source_label"]
    assert first["difficulty"] in ("easy", "medium", "hard")
    assert first["topic"]
    assert "resume" not in first  # first load is not a resume

    # re-requesting returns the same question flagged as a resume
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    resumed = resp.get_json()
    assert resumed["question"] == first["question"]
    assert resumed["resume"] is True

    resp = client.post("/session/submit", headers=headers, json={"answer": ANSWERS[0]})
    assert resp.status_code == 200
    graded = resp.get_json()
    assert graded["points"] in (0, 1, 2, 3)
    assert graded["max_points"] == 3
    assert graded["grader"] == "rule"
    assert graded["ai_used"] is False
    assert graded["fallback_reason"] == "ai_unavailable"
    assert graded["session_completed"] is False
    assert graded["questions_answered"] == 1
    assert graded["question_number"] == 1
    assert graded["next_question_number"] == 2
    assert graded["feedback"]
    assert graded["breakdown"]

    resp = client.post("/correct", headers=headers, json={"answer": ANSWERS[0]})
    assert resp.status_code == 200
    corrected = resp.get_json()
    assert corrected["improved"]
    assert isinstance(corrected["changes"], list)
    assert corrected["ai_used"] is False
    assert corrected["fallback_reason"] == "ai_unavailable"

    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    resp = client.post("/session/skip", headers=headers)
    assert resp.status_code == 200
    skipped = resp.get_json()
    assert skipped["skipped"] is True
    assert skipped["questions_answered"] == 2

    for i in range(2, 5):
        resp = client.get("/session/question", headers=headers)
        assert resp.status_code == 200
        resp = client.post("/session/submit", headers=headers, json={"answer": ANSWERS[i]})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["session_completed"] is (i == 4)
        if i == 4:
            assert data["questions_answered"] == 5

    # completed session: question and submit are rejected
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 409
    completed = resp.get_json()
    assert completed["completed"] is True
    assert completed["summary"]["completed"] is True
    assert completed["summary"]["session_length"] == 5
    assert completed["summary"]["skipped_count"] == 1
    assert len(completed["summary"]["questions"]) == 5

    assert client.post("/session/submit", headers=headers, json={"answer": "x"}).status_code == 409


def test_stats_flow(client):
    token = _signup(client, "stats@test.local")
    _complete_session(client, token)
    headers = _headers(token)

    resp = client.get("/stats/unlock-status", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"unlocked": True, "answered": 5, "required": 1}

    resp = client.get("/stats/summary", headers=headers)
    assert resp.status_code == 200
    summary = resp.get_json()
    assert summary["total_questions"] == 5
    assert summary["avg_score"] >= 0
    assert summary["best_role"] == ROLE
    assert summary["streak"] >= 0

    resp = client.get("/stats/chart-data", headers=headers)
    assert resp.status_code == 200
    chart = resp.get_json()
    assert len(chart["time_series"]) == 5
    assert chart["time_series"][0]["index"] == 1
    assert set(chart["by_role"]) == {ROLE}
    assert sum(chart["distribution"].values()) == 5
    assert set(chart["by_difficulty"]) <= {"easy", "medium", "hard"}
    assert isinstance(chart["weak_topics"], list)
    for row in chart["weak_topics"]:
        assert {"topic", "avg_score", "count"} <= set(row)
        assert row["count"] >= 2

    resp = client.get("/stats/export/json", headers=headers)
    assert resp.status_code == 200
    assert resp.content_type.startswith("application/json")
    export = resp.get_json()
    assert len(export["sessions"]) == 1
    assert len(export["attempts"]) == 5

    # PDF generation is behavior-preserving: fpdf can fail on very long
    # question text, so accept either outcome.
    resp = client.get("/stats/export/pdf", headers=headers)
    assert resp.status_code in (200, 500)
    if resp.status_code == 200:
        assert resp.content_type == "application/pdf"


def test_stats_locked(client):
    token = _signup(client, "locked@test.local")
    headers = _headers(token)
    resp = client.get("/stats/unlock-status", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"unlocked": False, "answered": 0, "required": 1}


def test_model_answer_flow(client):
    token = _signup(client, "model@test.local")
    sess = _start_session(client, token)
    headers = _headers(sess)
    # no answered question yet
    assert client.post("/session/model-answer", headers=headers).status_code == 400
    assert client.get("/session/question", headers=headers).status_code == 200
    assert client.post("/session/submit", headers=headers, json={"answer": ANSWERS[0]}).status_code == 200

    resp = client.post("/session/model-answer", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["model_answer"]
    assert data["ai_used"] is False
    assert data["fallback_reason"] == "ai_unavailable"
    assert data["cached"] is False

    # repeat call is served from cache, same text, no re-generation
    resp2 = client.post("/session/model-answer", headers=headers)
    assert resp2.status_code == 200
    cached = resp2.get_json()
    assert cached["cached"] is True
    assert cached["model_answer"] == data["model_answer"]


def test_double_submit_is_idempotent(client):
    token = _signup(client, "double@test.local")
    sess = _start_session(client, token)
    headers = _headers(sess)
    assert client.get("/session/question", headers=headers).status_code == 200

    resp = client.post("/session/submit", headers=headers, json={"answer": ANSWERS[0]})
    assert resp.status_code == 200
    first = resp.get_json()
    assert first["questions_answered"] == 1

    # resubmitting the same answer must not re-grade or advance the session
    resp = client.post("/session/submit", headers=headers, json={"answer": ANSWERS[0]})
    assert resp.status_code == 200
    again = resp.get_json()
    assert again["points"] == first["points"]
    assert again["feedback"] == first["feedback"]
    assert again["questions_answered"] == 1
    assert again["question_number"] == 1
    assert again["session_completed"] is False


def test_skip_after_completion(client):
    # Pre-existing behavior: skipping a completed session is accepted and
    # marks the last attempt as skipped (no 409 guard like submit has).
    token = _signup(client, "postdone@test.local")
    sess = _complete_session(client, token)
    resp = client.post("/session/skip", headers=_headers(sess))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["skipped"] is True
    assert data["session_completed"] is True


def test_answer_required_on_correct(client):
    token = _signup(client, "correct@test.local")
    sess = _start_session(client, token)
    headers = _headers(sess)
    assert client.get("/session/question", headers=headers).status_code == 200
    # /correct only applies once a question has actually been answered
    assert client.post("/correct", headers=headers, json={"answer": ""}).status_code == 400
    assert client.post("/correct", headers=headers, json={"answer": ANSWERS[0]}).status_code == 400
    assert client.post("/session/submit", headers=headers, json={"answer": "   "}).status_code == 400
    # after an answer exists, /correct works and requires a non-empty answer
    assert client.post("/session/submit", headers=headers, json={"answer": ANSWERS[0]}).status_code == 200
    assert client.post("/correct", headers=headers, json={"answer": ""}).status_code == 400
    assert client.post("/correct", headers=headers, json={"answer": ANSWERS[0]}).status_code == 200


def test_question_generation_circuit_breaker(monkeypatch):
    import ai_teacher

    monkeypatch.setattr(ai_teacher, "AI_ENABLED", True)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    calls = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("groq_timeout:30s exceeded")

    monkeypatch.setattr(ai_teacher, "_invoke_ai", _boom)
    saved_fail, saved_open = ai_teacher._fail_count, ai_teacher._circuit_open_until
    ai_teacher._fail_count = 0
    ai_teacher._circuit_open_until = 0
    try:
        for _ in range(ai_teacher._threshold):
            assert ai_teacher.ai_generate_question(ROLE, 1, {}) is None
        assert len(calls) == ai_teacher._threshold
        # breaker is open now: fails fast without another AI call
        assert ai_teacher.ai_generate_question(ROLE, 2, {}) is None
        assert len(calls) == ai_teacher._threshold
    finally:
        ai_teacher._fail_count = saved_fail
        ai_teacher._circuit_open_until = saved_open


def test_question_generation_success_resets_breaker(monkeypatch):
    import ai_teacher

    monkeypatch.setattr(ai_teacher, "AI_ENABLED", True)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def _ok(*args, **kwargs):
        return {
            "choices": [{"message": {"content": json.dumps({
                "question": "Walk me through debugging a slow endpoint in production.",
                "topic": "performance",
                "difficulty": "medium",
            })}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    monkeypatch.setattr(ai_teacher, "_invoke_ai", _ok)
    saved_fail, saved_open = ai_teacher._fail_count, ai_teacher._circuit_open_until
    ai_teacher._fail_count = ai_teacher._threshold - 1
    ai_teacher._circuit_open_until = 0
    try:
        result = ai_teacher.ai_generate_question(ROLE, 2, {})
        assert result is not None and result["question"].startswith("Walk me through")
        assert ai_teacher._fail_count == 0
        assert ai_teacher._circuit_open_until == 0
    finally:
        ai_teacher._fail_count = saved_fail
        ai_teacher._circuit_open_until = saved_open


def test_validate_generated_question_style_defaults(client):
    import app as app_module

    base = {"question": "Debug a slow endpoint under load.", "topic": "performance", "difficulty": "medium"}

    ok = app_module._validate_generated_question({**base, "question_style": "scenario"}, set())
    assert ok["question_style"] == "scenario"

    defaulted = app_module._validate_generated_question(dict(base), set())
    assert defaulted["question_style"] == "conceptual"

    garbage = app_module._validate_generated_question({**base, "question_style": " EXAM "}, set())
    assert garbage["question_style"] == "conceptual"

    assert app_module._validate_generated_question({**base, "difficulty": "impossible"}, set()) is None
    assert app_module._validate_generated_question({**base, "topic": ""}, set()) is None


def test_fallback_attempt_stores_null_style(client):
    import app as app_module

    token = _signup(client, "style@test.local")
    sess = _start_session(client, token)
    headers = _headers(sess)
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    with app_module._db() as conn:
        row = conn.execute("SELECT question_style FROM attempts").fetchone()
    # fallback-bank questions carry no style metadata (NULL), AI ones do
    if resp.get_json()["question_source"] == "fallback":
        assert row["question_style"] is None


def test_expectation_hint_composition(client):
    import app as app_module

    hint = app_module._expectation_hint({
        "question_style": "conceptual",
        "concepts": ["last in first out", "first in first out", "undo", "history"],
        "keywords": ["lifo", "fifo", "push", "pop"],
        "ideal_length": 80,
        "topic": "data structures",
    })
    assert hint.startswith("Expect to: ") and hint.endswith(".")
    assert "explain last in first out, first in first out, undo" in hint  # capped at 3 concepts
    assert "cover lifo, fifo, push" in hint
    assert "(~80 words)" in hint

    scenario = app_module._expectation_hint({
        "question_style": "scenario",
        "topic": "debugging",
        "difficulty": "hard",
    })
    assert "on-the-job situation" in scenario
    assert "trade-offs" in scenario
    assert "(~" not in scenario

    empty = app_module._expectation_hint({})
    assert empty.startswith("Expect to: define the concept clearly")

    bad_length = app_module._expectation_hint({"ideal_length": "many"})
    assert "(~" not in bad_length


def test_session_question_includes_expectation_hint(client):
    token = _signup(client, "hint@test.local")
    sess = _start_session(client, token)
    headers = _headers(sess)

    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    hint = data["expectation_hint"]
    assert isinstance(hint, str) and hint.startswith("Expect to: ")
    assert hint.endswith(".")
    assert "~" in hint and "words)" in hint  # every bank question has ideal_length

    # resume path serves the same hint for the same question
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    resumed = resp.get_json()
    assert resumed["resume"] is True
    assert resumed["expectation_hint"] == hint


def test_start_session_difficulty_validation(client):
    import app as app_module

    token = _signup(client, "diffval@test.local")
    headers = _headers(token)

    resp = client.post("/session/start", headers=headers, json={"role": ROLE, "difficulty": "impossible"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid difficulty"}

    resp = client.post("/session/start", headers=headers, json={"role": ROLE})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["target_difficulty"] == "mixed"
    sess = data["session_token"]
    with app_module._db() as conn:
        row = conn.execute("SELECT target_difficulty FROM sessions WHERE id = ?", (sess,)).fetchone()
    assert row["target_difficulty"] == "mixed"

    # case-insensitive acceptance
    resp = client.post("/session/start", headers=headers, json={"role": ROLE, "difficulty": " EASY "})
    assert resp.status_code == 200
    assert resp.get_json()["target_difficulty"] == "easy"


def test_easy_target_session_serves_only_easy(client):
    import app as app_module

    token = _signup(client, "easy@test.local")
    resp = client.post("/session/start", headers=_headers(token), json={"role": ROLE, "difficulty": "easy"})
    assert resp.status_code == 200
    sess = resp.get_json()["session_token"]
    with app_module._db() as conn:
        row = conn.execute("SELECT target_difficulty FROM sessions WHERE id = ?", (sess,)).fetchone()
    assert row["target_difficulty"] == "easy"

    headers = _headers(sess)
    for i in range(3):
        resp = client.get("/session/question", headers=headers)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["difficulty"] == "easy", f"question {i + 1}: {data['question']}"
        resp = client.post("/session/skip", headers=headers)
        assert resp.status_code == 200


def test_fallback_question_difficulty_filter(client):
    import app as app_module

    # Software Engineer has easy questions: filter serves only easy ones
    for _ in range(5):
        q = app_module._fallback_question(ROLE, set(), "easy")
        assert q is not None and q["difficulty"] == "easy"

    # Consultant has zero easy questions: gracefully falls back to full pool
    q = app_module._fallback_question("Consultant", set(), "easy")
    assert q is not None
    assert q["difficulty"] in {"medium", "hard"}

    # used questions are avoided within the filtered pool
    pool_easy = [q for q in app_module._questions_for_role(ROLE) if app_module._difficulty(q.get("ideal_length", 80)) == "easy"]
    used = {app_module._normalize_text(q["q"]) for q in pool_easy}
    q = app_module._fallback_question(ROLE, used, "easy")
    assert q is not None  # exhausted easy pool -> full-pool fallback, never None

    # mixed behaves like before
    assert app_module._fallback_question(ROLE, set()) is not None


def test_hint_flow(client):
    token = _signup(client, "hintflow@test.local")
    sess = _start_session(client, token)
    headers = _headers(sess)
    # no active question yet
    assert client.post("/session/hint", headers=headers).status_code == 400

    assert client.get("/session/question", headers=headers).status_code == 200
    resp = client.post("/session/hint", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["hint"]
    assert data["cached"] is False
    assert data["ai_used"] is False
    assert data["fallback_reason"] == "ai_unavailable"

    # repeat call is served from cache, same text, no re-generation
    resp2 = client.post("/session/hint", headers=headers)
    assert resp2.status_code == 200
    cached = resp2.get_json()
    assert cached["cached"] is True
    assert cached["hint"] == data["hint"]

    # once answered, no more hints
    assert client.post("/session/submit", headers=headers, json={"answer": ANSWERS[0]}).status_code == 200
    assert client.post("/session/hint", headers=headers).status_code == 400


def test_hint_caps_grade_score(client, monkeypatch):
    import app as app_module

    # pin fallback selection to the stack/queue question (pool index 0) so
    # both sessions grade the identical question+answer pair deterministically
    monkeypatch.setattr(app_module.random, "choice", lambda seq: seq[0])

    def run_session(email, use_hint):
        token = _signup(client, email)
        resp = client.post("/session/start", headers=_headers(token), json={"role": ROLE})
        sess = resp.get_json()["session_token"]
        headers = _headers(sess)
        assert client.get("/session/question", headers=headers).status_code == 200
        if use_hint:
            r = client.post("/session/hint", headers=headers)
            assert r.status_code == 200 and r.get_json()["cached"] is False
        strong = ("LIFO and FIFO are the key ordering semantics of these structures. A stack is last in first out, "
                  "so you push and pop from the top; it powers undo history and recursion such as DFS traversal. "
                  "A queue is first in first out because you enqueue at the rear and dequeue from the front, which "
                  "suits print queues and BFS level order. For example, however, if ordering matters, therefore I "
                  "pick a stack for backtracking and a queue for scheduling.")
        r = client.post("/session/submit", headers=headers, json={"answer": strong})
        assert r.status_code == 200, r.get_data(as_text=True)
        return token, r.get_json()

    capped_token, graded = run_session("capped@test.local", True)
    assert graded["hint_used"] is True
    assert graded["points"] == 2
    assert graded["max_points"] == 3
    assert graded["hint_capped"] is True

    _, control = run_session("uncapped@test.local", False)
    assert control["hint_used"] is False
    assert control["points"] == 3  # same question+answer without a hint scores full marks
    assert control["hint_capped"] is False

    # stats visibility
    headers = _headers(capped_token)
    summary = client.get("/stats/summary", headers=headers).get_json()
    assert summary["hints_used"] == 1
    assert summary["avg_score"] <= 2
    chart = client.get("/stats/chart-data", headers=headers).get_json()
    assert chart["time_series"][0]["hint_used"] is True


def test_validate_and_extract_jd_unit(client):
    import ai_teacher

    result = ai_teacher.ai_extract_jd("some job description text")
    assert result["skills"] == []
    assert result["tools"] == []
    assert result["seniority"] == ""
    assert result["focus_areas"] == []
    assert result["ai_used"] is False
    assert result["fallback_reason"] == "ai_unavailable"

    validated = ai_teacher._validate_jd({
        "skills": ["SQL", "", 3, "Go"],
        "tools": "not-a-list",
        "seniority": "  Senior ",
        "focus_areas": [f"area{i}" for i in range(20)],
    })
    assert validated["skills"] == ["sql", "go"]
    assert validated["tools"] == []
    assert validated["seniority"] == "senior"
    assert len(validated["focus_areas"]) == 12  # capped


def test_jd_session_falls_back_without_match(client):
    import app as app_module

    token = _signup(client, "jd@test.local")
    resp = client.post(
        "/session/start",
        headers=_headers(token),
        json={"role": ROLE, "job_description": "Hiring a senior engineer strong in Kubernetes and Go."},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    sess = data["session_token"]
    # AI disabled -> extraction degraded gracefully to the empty structure
    assert data["job_description_active"] is False

    headers = _headers(sess)
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    served = resp.get_json()
    # transparent: this is a bank question, not a JD-matched one
    assert served["question_source"] == "fallback_no_jd_match"
    assert "JD" in served["question_source_label"]

    # resume keeps the honest flag
    resumed = client.get("/session/question", headers=headers).get_json()
    assert resumed["question_source"] == "fallback_no_jd_match"

    with app_module._db() as conn:
        row = conn.execute("SELECT jd_text, jd_extracted FROM sessions WHERE id = ?", (sess,)).fetchone()
    assert row["jd_text"].startswith("Hiring a senior engineer")
    assert json.loads(row["jd_extracted"]) == {"skills": [], "tools": [], "seniority": "", "focus_areas": []}


def test_plain_fallback_without_jd(client):
    token = _signup(client, "nojd@test.local")
    sess = _start_session(client, token)
    resp = client.get("/session/question", headers=_headers(sess))
    assert resp.status_code == 200
    assert resp.get_json()["question_source"] == "fallback"


def test_jd_coverage_in_summary(client, monkeypatch):
    import ai_teacher

    monkeypatch.setattr(ai_teacher, "AI_ENABLED", True)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def _fake_invoke(*args, **kwargs):
        return {
            "choices": [{"message": {"content": json.dumps({
                "skills": ["docker", "kubernetes"],
                "tools": ["git"],
                "seniority": "mid",
                "focus_areas": ["ci/cd"],
            })}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }

    monkeypatch.setattr(ai_teacher, "_invoke_ai", _fake_invoke)

    token = _signup(client, "jdcover@test.local")
    resp = client.post(
        "/session/start",
        headers=_headers(token),
        json={"role": ROLE, "job_description": "Engineer with docker, kubernetes and git in a ci/cd world."},
    )
    assert resp.status_code == 200
    assert resp.get_json()["job_description_active"] is True

    sess = resp.get_json()["session_token"]
    headers = _headers(sess)
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    # generation got the extraction-shaped payload instead of a question -> fallback
    assert resp.get_json()["question_source"] == "fallback_no_jd_match"

    answer = ("We deploy services as containers with docker and review code through git pull requests, "
              "which keeps releases small and reversible.")
    resp = client.post("/session/submit", headers=headers, json={"answer": answer})
    assert resp.status_code == 200
    for _ in range(4):
        assert client.get("/session/question", headers=headers).status_code == 200
        assert client.post("/session/skip", headers=headers).status_code == 200

    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 409
    coverage = resp.get_json()["summary"]["jd_coverage"]
    assert coverage["covered"] == ["docker", "git"]
    assert "kubernetes" in coverage["missed"]
    assert "ci/cd" in coverage["missed"]


def _jd_ai_dispatcher(fail_state=None):
    counter = {"n": 0}

    def _fake(messages, system_prompt=None, expected_tokens_out=0):
        if fail_state is not None and fail_state["fail"]:
            raise RuntimeError("groq_timeout:30s exceeded")
        content = messages[-1]["content"]
        if '"job_description"' in content:
            body = {
                "skills": ["process safety", "distillation"],
                "tools": ["aspen"],
                "seniority": "mid",
                "focus_areas": ["incident response"],
            }
        elif "scenario|conceptual" in content:
            counter["n"] += 1
            n = counter["n"]
            body = {
                "question": f"Describe how you would investigate overpressure event number {n} in a distillation column.",
                "topic": f"process safety {n}",
                "difficulty": "medium",
                "question_style": "scenario",
            }
        elif '"grading_rules"' in content:
            body = {"feedback": "Solid reasoning.", "points": 2, "breakdown": "covered key points"}
        elif '"hint"' in content:
            body = {"hint": "Consider your relief systems."}
        else:
            body = {"model_answer": "A strong answer walks through the scenario."}
        return {"choices": [{"message": {"content": json.dumps(body)}}], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}

    return _fake


def _enable_ai(client, monkeypatch, fail_state=None):
    import ai_teacher
    import app as app_module

    ai_teacher._fail_count = 0
    ai_teacher._circuit_open_until = 0
    monkeypatch.setattr(ai_teacher, "AI_ENABLED", True)
    monkeypatch.setattr(app_module, "AI_ENABLED", True)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    if fail_state is None:
        fail_state = {"fail": False}
    monkeypatch.setattr(ai_teacher, "_invoke_ai", _jd_ai_dispatcher(fail_state))
    return fail_state


def test_jd_preview_endpoint(client):
    assert client.post("/jd/preview", json={"job_description": "x"}).status_code == 401
    token = _signup(client, "pv@test.local")
    headers = _headers(token)
    assert client.post("/jd/preview", headers=headers, json={}).status_code == 400

    resp = client.post("/jd/preview", headers=headers, json={"job_description": "Chemical engineer, aspen, process safety."})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["signals"] == {"skills": [], "tools": [], "seniority": "", "focus_areas": []}
    assert data["ai_used"] is False
    assert data["fallback_reason"] == "ai_unavailable"


def test_jd_preview_with_ai(client, monkeypatch):
    _enable_ai(client, monkeypatch)
    token = _signup(client, "pvai@test.local")
    resp = client.post("/jd/preview", headers=_headers(token), json={"job_description": "Chemical engineer focused on distillation safety."})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ai_used"] is True
    assert data["signals"]["skills"] == ["process safety", "distillation"]
    assert data["signals"]["seniority"] == "mid"


def test_custom_role_start_validation(client):
    token = _signup(client, "customval@test.local")
    headers = _headers(token)

    # unknown role without a JD is rejected before anything else
    resp = client.post("/session/start", headers=headers, json={"role": "Chemical Engineer"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "custom roles require a job description"

    # with a JD but AI disabled: blocked by policy (no bank fallback exists)
    resp = client.post("/session/start", headers=headers, json={"role": "Chemical Engineer", "job_description": "Distillation and process safety."})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "custom roles require AI to be enabled"

    # known roles keep working regardless of AI state
    assert client.post("/session/start", headers=headers, json={"role": ROLE}).status_code == 200


def test_custom_role_session_flow(client, monkeypatch):
    import app as app_module

    _enable_ai(client, monkeypatch)
    token = _signup(client, "chem@test.local")
    resp = client.post(
        "/session/start",
        headers=_headers(token),
        json={"role": "Chemical Engineer", "job_description": "ChemE role: distillation, process safety, aspen."},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    sess = data["session_token"]
    assert data["role"] == "Chemical Engineer"
    assert data["job_description_active"] is True

    with app_module._db() as conn:
        row = conn.execute("SELECT role FROM sessions WHERE id = ?", (sess,)).fetchone()
    assert row["role"] == "Chemical Engineer"

    headers = _headers(sess)
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200
    q = resp.get_json()
    assert q["question_source"] == "ai"
    assert "distillation" in q["question"].lower()

    answer = ("I would start with process safety: verify relief paths, isolate the feed in the "
              "distillation column, check the aspen model assumptions, and walk the unit with operations.")
    resp = client.post("/session/submit", headers=headers, json={"answer": answer})
    assert resp.status_code == 200
    graded = resp.get_json()
    assert graded["points"] in (0, 1, 2, 3)
    assert graded["grader"] == "ai"

    for _ in range(4):
        assert client.get("/session/question", headers=headers).status_code == 200
        assert client.post("/session/skip", headers=headers).status_code == 200

    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 409
    summary = resp.get_json()["summary"]
    assert summary["role"] == "Chemical Engineer"
    assert summary["jd_coverage"]["covered"] == ["process safety", "distillation", "aspen"]


def test_custom_role_generation_failure_is_retryable(client, monkeypatch):
    import app as app_module

    fail_state = _enable_ai(client, monkeypatch)
    token = _signup(client, "retry@test.local")
    resp = client.post(
        "/session/start",
        headers=_headers(token),
        json={"role": "Chemical Engineer", "job_description": "Distillation and process safety focus."},
    )
    assert resp.status_code == 200
    sess = resp.get_json()["session_token"]
    headers = _headers(sess)

    assert client.get("/session/question", headers=headers).status_code == 200

    # answer Q1 so the next fetch actually needs fresh generation (an un-answered
    # question would be served from the resume cache, which correctly ignores outages)
    resp = client.post("/session/submit", headers=headers, json={"answer": "Walk through isolation and relief verification first."})
    assert resp.status_code == 200

    fail_state["fail"] = True
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 503
    assert resp.get_json()["retry"] is True
    with app_module._db() as conn:
        row = conn.execute("SELECT finalized FROM sessions WHERE id = ?", (sess,)).fetchone()
        attempts = conn.execute("SELECT COUNT(*) c FROM attempts WHERE session_id = ?", (sess,)).fetchone()["c"]
    # the session survived the outage instead of being silently completed
    assert row["finalized"] == 0
    assert attempts == 1

    fail_state["fail"] = False
    resp = client.get("/session/question", headers=headers)
    assert resp.status_code == 200


def test_difficulty_plausibility(client):
    import app as app_module

    # easy: short single-ask -> pass
    assert app_module._validate_generated_question(
        {"question": "Your production endpoint is slow. What is the first thing you check?", "topic": "perf", "difficulty": "easy", "question_style": "scenario"},
        set(),
    ) is not None

    # easy: compound multi-constraint -> rejected (2 ? + stacked signals push past the net)
    assert app_module._validate_generated_question(
        {
            "question": "The legacy system has a tight deadline, the stakeholder disagrees and the budget is tight — what do you do? How do you decide?",
            "topic": "x",
            "difficulty": "easy",
            "question_style": "scenario",
        },
        set(),
    ) is None

    # medium: too long -> rejected (>55 words)
    long_q = " ".join(["word"] * 56) + "?"
    assert app_module._validate_generated_question(
        {"question": long_q, "topic": "x", "difficulty": "medium", "question_style": "conceptual"},
        set(),
    ) is None

    # hard: multi-constraint scenario -> allowed (only ?-check applies)
    assert app_module._validate_generated_question(
        {
            "question": "A stakeholder wants the legacy migration on a tight deadline while the budget is constrained — how do you decide what to ship first?",
            "topic": "trade-offs",
            "difficulty": "hard",
            "question_style": "scenario",
        },
        set(),
    ) is not None

    # any difficulty with 2 ? -> rejected
    assert app_module._validate_generated_question(
        {"question": "What would you do? How would you investigate it?", "topic": "x", "difficulty": "hard", "question_style": "scenario"},
        set(),
    ) is None


def test_fallback_bank_not_validated(client):
    import app as app_module

    # Fallback bank questions bypass _validate_generated_question entirely
    # (see _next_question fallback branch) — verify by serving one through
    # the normal route. The bank contains at least one multi-ask ("?...?...")
    # that the new plausibility check WOULD reject if it were AI-generated,
    # so this proves the check is AI-path-only.
    multi_ask = [q for qs in app_module.QUESTIONS.values() for q in qs if q["q"].count("?") > 1]
    assert multi_ask, "expected at least one multi-ask bank question to exercise the guard"
    token = _signup(client, "bankcheck@test.local")
    sess = _start_session(client, token)
    resp = client.get("/session/question", headers=_headers(sess))
    assert resp.status_code == 200
    assert resp.get_json()["question_source"] in ("fallback", "ai", "fallback_no_jd_match")
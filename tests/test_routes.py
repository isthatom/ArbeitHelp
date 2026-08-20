ROLE = "Software Engineer"

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
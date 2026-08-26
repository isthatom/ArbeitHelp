# ArbeitHelp Agent Guide

## Project shape

- This is a Flask application with a plain HTML/CSS/JavaScript frontend; there is no frontend build step.
- `app.py` owns Flask routes, authentication, session state, SQLite access, and fallback grading.
- `ai_teacher.py` owns Groq calls, prompt loading, AI response parsing, timeouts, and AI fallbacks.
- `config.py` centralizes environment-backed settings and repository paths.
- `static/` contains the browser pages and scripts. Keep shared browser behavior in `common.js` and page-specific behavior in the matching script and stylesheet.
- `data/questions.json` is the fallback question bank; `prompts/` contains the versioned AI prompts.
- `tests/` contains pytest route and workflow coverage. See [README.md](README.md) for the product overview and setup details.

## Development commands

```powershell
pip install -r requirements.txt
python app.py
pytest
```

The development server listens on `http://localhost:5050`. Production startup uses `gunicorn app:app`.

## Working conventions

- Preserve the existing route and JSON API contracts unless the task explicitly changes them. Add or update route tests for contract changes.
- Keep AI-disabled behavior working. The application must continue to serve the static question bank and rule-based grading when `AI_ENABLED=false` or `GROQ_API_KEY` is absent.
- For AI changes, preserve timeout handling, response validation, usage metadata, and the existing fallback path. Prompts are external files, not inline strings.
- Use parameterized SQLite queries and close database work through the existing `_db()` context-manager pattern.
- Keep browser code framework-free and use DOM APIs consistent with the existing scripts. Escape or set user-controlled content as text rather than injecting HTML.
- Avoid editing `data/app_data.sqlite3` as part of development. Tests must remain hermetic and must not require a Groq API key.
- Do not commit `.env`, API keys, generated JWT secrets, or local database changes.

## Testing notes

`tests/conftest.py` disables AI, supplies a stable test JWT secret, and redirects the app to a temporary SQLite database before the shared test client is created. Keep tests deterministic and use the existing authorization header/session helpers when extending route coverage.

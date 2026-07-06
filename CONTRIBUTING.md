# Contributing to Plus One

Thanks for helping improve Plus One. This project is a Django app for anonymous campus activity matching, so changes should keep the core flow simple, fast, and safe.

## Local Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

## Environment Variables

Use a local `.env` file for secrets. Do not commit real API keys.

```env
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
PLUSONE_LLM_MODEL=deepseek-v4-flash
DJANGO_DEBUG=True
```

If no LLM key is configured, the app falls back to deterministic local behavior for development.

## Development Checks

Run these before opening a pull request:

```bash
.venv/bin/python manage.py check
node --check plusone/static/plusone/app.js
.venv/bin/python manage.py test
```

For CI-like SQLite checks:

```bash
DATABASE_URL= ALLOWED_HOSTS=127.0.0.1,localhost,testserver .venv/bin/python manage.py test --noinput
```

## Pull Request Guidelines

- Keep changes scoped to one product or engineering problem.
- Avoid committing `.env`, local databases, virtual environments, or generated `staticfiles/`.
- Add or update tests when changing matching, chat, moderation, time parsing, dashboard status, or deployment behavior.
- Keep user-facing copy clear and non-technical.
- Confirm the Render deployment path still works when touching `render.yaml`, `build.sh`, or production settings.

## Product Principles

- Preserve the one-to-one "Plus One" matching model.
- Keep anonymous sessions easy to understand.
- Moderate unsafe post and chat content before accepting it.
- Make Dashboard states action-oriented: open chat, handoff, ended, or history.
- Prefer simple Django-native patterns over unnecessary frontend complexity.

# Security Policy

Plus One handles anonymous sessions, temporary activity cards, chat messages, and server-side LLM calls. Please report security issues responsibly.

## Supported Version

The currently supported version is the latest deployed branch in this repository.

## Reporting a Vulnerability

Do not open a public issue for vulnerabilities, leaked secrets, authentication problems, unsafe moderation bypasses, or private data exposure.

Instead, contact the repository owner privately through GitHub and include:

- a short summary,
- affected page or endpoint,
- steps to reproduce,
- expected impact,
- screenshots or logs if safe to share.

## Sensitive Data

Never commit:

- `.env` files,
- `DEEPSEEK_API_KEY`,
- `OPENAI_API_KEY`,
- database dumps,
- production session data,
- private chat logs.

If a key is exposed, rotate it immediately in the provider dashboard and update the hosting environment variable.

## AI Safety Scope

Plus One uses AI-assisted parsing, icebreakers, and safety moderation. Reports about harmful content passing through post creation or chat moderation are treated as security and safety issues.

Useful reports include:

- unsafe post text accepted by the create flow,
- unsafe chat text stored or displayed,
- prompt injection causing unexpected structured output,
- moderation logs missing for accepted or blocked content.

## Deployment Security

Production deployments should keep:

- `DJANGO_DEBUG=False`,
- a strong `SECRET_KEY`,
- `DEEPSEEK_API_KEY` configured only in the hosting dashboard,
- HTTPS enabled,
- `DATABASE_URL` pointing to managed PostgreSQL,
- demo seeding disabled in production builds.

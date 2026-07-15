# API Contract

All routes are server-rendered Django views unless marked JSON. Anonymous
session identity is created on first visit; every route below assumes it.
CSRF token required on all POSTs.

## Pages and Actions

| Route | Method | Purpose / Parameters | Response |
| --- | --- | --- | --- |
| `/` | GET | Redirect to Discover | 302 |
| `/discover/` | GET | Queue of active cards. Query: `activity_type`, `location` (id), `time_window` (`now`/`today`), `matched` (match id -> match modal) | HTML |
| `/create/` | GET | Create form | HTML |
| `/create/` | POST `action=assist` | `raw_text` -> moderated, AI-parsed draft with `validation` warnings | HTML (form initial + warnings) |
| `/create/` | POST `action=publish` | Form fields + hidden `raw_text`, optional `confirm_date=yes`. Blocked once when text date conflicts with `start_time` | 302 to post detail, or HTML with blocker |
| `/posts/<id>/` | GET | Card detail | HTML |
| `/posts/<id>/edit/` | GET/POST | Owner-only edit / cancel | HTML / 302 |
| `/posts/<id>/swipe/` | POST | `action=interested\|pass`. Outcomes: match_created, match_exists, passed, own_post, full_post, inactive_post, try_again | 302 (Discover with `matched=<id>` on match) |
| `/posts/<id>/undo-pass/` | POST | Restore last passed card | 302 |
| `/dashboard/` `/matches/` | GET | Active/matched/expired/cancelled overview | HTML |
| `/session/` `/profile/setup/` | GET/POST | Session status; identity reset via `/identity/reset/` POST (closes live cards + chats) | HTML / 302 |

## Chat

| Route | Method | Purpose / Parameters | Response |
| --- | --- | --- | --- |
| `/chat/<match_id>/` | GET | Chat page (participants only, else 403) | HTML |
| `/chat/<match_id>/` | POST `action=send` | `message` (<=500 chars, moderated; unsafe not persisted) | 302 |
| `/chat/<match_id>/` | POST `action=agree` | Record agreement; both sides -> AGREED + handoff | 302 |
| `/chat/<match_id>/` | POST `action=decline\|report` | Close match with reason; system message appended | 302 |
| `/chat/<match_id>/` | POST `action=suggest_openers` | AI suggestions (first-message or reply mode); never persisted, never auto-sent | HTML with suggestion cards |
| `/chat/<match_id>/messages/` | GET (JSON) | Poll: `after` (message id). Returns `{ok, messages[], chat_status, chat_active}` | JSON |
| `/chat/<match_id>/messages/` | POST (JSON) | Async send: `message`. 400 with `{flagged, warning}` when moderated; 409 when chat closed | JSON |
| `/chat/<match_id>/opener-click/` | POST (JSON) | Analytics only: `index` of clicked suggestion | `{ok: true}` |

## Invariants

- Participants-only: every chat route checks `match.is_participant(user)`.
- One match per (post, swiper); post owner cannot swipe own post.
- Chat writes are refused (409/redirect) once status leaves CHATTING.
- Moderation-blocked content is never persisted; the moderation decision is
  logged to `LLMLog`.
- Demo-mode writes never emit `ProductEvent` rows.

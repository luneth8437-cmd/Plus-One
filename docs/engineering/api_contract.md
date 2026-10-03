# API Contract

All routes are server-rendered Django views unless marked JSON. Anonymous
session identity is created on normal entry pages, never by health/readiness or session-update polling.
CSRF token required on all POSTs.

## Pages and Actions

| Route | Method | Purpose / Parameters | Response |
| --- | --- | --- | --- |
| `/` | GET | Redirect to Discover | 302 |
| `/discover/` | GET | Queue of active cards. Query: `activity_type`, `location` (id), `time_window` (`now`/`today`), `matched` (match id -> match modal) | HTML |
| `/create/` | GET | Create form | HTML |
| `/create/` | POST `action=assist` | `raw_text` -> moderated, AI-parsed draft with `validation` warnings | HTML (form initial + warnings) |
| `/create/` | POST `action=publish` | UUID `request_id`, form fields, `raw_text` (max 2,000), optional `confirm_date=yes`; final description max 2,000 | 302 to detail; HTML preserving input on failure |
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
| `/chat/<match_id>/` | POST `action=send` | UUID `request_id`, `message` (<=500 chars, moderated) | 302 or bound form with error |
| `/chat/<match_id>/` | POST `action=agree` | Legacy compatibility for an unchanged revision-1 plan; edited plans require versioned confirmation | 302 |
| `/chat/<match_id>/` | POST `action=decline` | End WAITING/CHATTING, release eligible card | 302 |
| `/chat/<match_id>/` | POST `action=report` | `category`: other/contact/harassment/unsafe_meeting; `reason` <=500; every phase allowed | 302; 400 invalid; 410 supplementation window expired |
| `/chat/<match_id>/` | POST `action=suggest_openers` | AI suggestions (first-message or reply mode); never persisted, never auto-sent | HTML with suggestion cards |
| `/chat/<match_id>/messages/` | GET (JSON) | Poll: nonnegative `after`; returns messages, next_cursor, server_time, phase, phase_deadline, viewer_agreed, other_agreed, viewer_present, other_present, and `plan`; retains chat_status/chat_active | JSON |
| `/chat/<match_id>/plan/` | POST | UUID `request_id`, integer `revision`, and meetup `action` (below); participants only | JSON when Accept includes application/json, otherwise 302; 400 invalid/unsafe, 403 unauthorized, 409 stale/conflicting/unavailable, 503 moderation unavailable |
| `/chat/<match_id>/messages/` | POST (JSON) | UUID `request_id`, `message`; returns original on same-ID/same-content replay, even after close | 200; 400 invalid/unsafe; 409 conflict/closed; 429 limited; 503 moderation unavailable |
| `/chat/<match_id>/presence/` | POST (JSON) | `visible=true\|false`, participants only; visible signals expire after 15 seconds | Current phase and state flags |
| `/session/updates/` | GET (JSON) | No identity creation; current identity's matches only | authenticated, matches[{id,url,status,title}], open_count, waiting_count, server_time |
| `/healthz/` | GET | Process liveness; no DB/session access | 200 text |
| `/readyz/` | GET | Database SELECT 1 only; no identity creation | 200 or 503 text |
| `/chat/<match_id>/opener-click/` | POST (JSON) | Analytics only: `index` of clicked suggestion | `{ok: true}` |

## Invariants

- Participants-only: every chat route checks `match.is_participant(user)`.
- One match per (post, swiper); post owner cannot swipe own post.
- New messages are refused once status leaves CHATTING. Successful UUID replays still return the original message. Missing/invalid IDs explicitly require refresh; changed content under the same ID returns 409.
- New matches WAITING for at most 10 minutes, bounded by card expiry. Only two recent foreground presence signals activate one five-minute clock; GET never marks arrival. WAITING and CHATTING hold capacity; terminal matches never restart.
- All message writes and phase changes take user locks in ascending order, then card, then match. Poll snapshots take the same lock. Clients advance the cursor from polling only, not from POST acknowledgements.
- Limits are shared database fixed windows: AI draft/suggestions/meeting-point moderation 6/min, publish/edit 10/hour, messages 30/min. Retry-After accompanies 429. Successful request replays do not consume quota; same UUID attempts are deduplicated within a bucket.
- Moderation-blocked content is never persisted; the moderation decision is
  logged to `LLMLog`.
- External moderation unavailable => 503 and no publication/message write. Rules mode requires explicit configuration and DEBUG=True. No production auto-demo exists.
- Reports are unique per participant/match; resubmitting a resolved report returns it to pending and retains handling notes. The original 90-day retention clock is not extended; after that, supplementation is explicitly rejected.

## Shared meeting plan

Every plan action supplies the displayed `revision` and a UUID `request_id`.
Same-ID/same-content replays return the stored action result with fresh state;
changed content under that ID or an obsolete revision returns 409. Successful
responses include the phase snapshot, `plan`, `action_result`, and `replayed`.
Error responses include current state when available so the client can ask the
participant to review it before choosing again.

| Action | Additional fields | Availability / result |
| --- | --- | --- |
| `update_plan` | `meeting_point` (1–160 chars), `meeting_at`, `expected_end_at` (ISO or campus-local datetime) | CHATTING, before the current meeting window ends; a changed plan increments the revision and clears both confirmations. Point is moderated before mutation. Times stay within the original activity window. |
| `confirm_plan` | none | CHATTING, before the current meeting window ends; both confirmations of the current revision produce AGREED. |
| `arrived` | none | Active AGREED meetup, thirty minutes before meeting through expected end; self-reported. |
| `delayed` | `delay_minutes`: 5, 10, or 0 to clear | Same window; does not change the plan time. |
| `cancel_meetup` | none | Active AGREED meetup through expected end; preserves agreement history and pauses the original card. |
| `reopen_card` | none | Publisher only, after cancellation, while original start/card expiry remain future and no other match holds capacity. |
| `outcome` | `outcome`: met/not_met; for not_met, `outcome_reason`: no_show/time_conflict/cancelled/other | From meeting time through expected end +24 hours; one self-report per participant. |

`plan` includes revision, point, original location, timestamps, campus-local
input/display strings, participant confirmations, arrival/delay/outcome state,
`meetup_status`, and `can_*` flags / `allowed_actions`. Polling continues after
agreement to synchronize meetup actions. Cancelling or reporting an AGREED
meetup leaves historical status AGREED, with `meetup_status=cancelled`; it no
longer holds capacity or appears as an active handoff.

Resetting an identity cancels its upcoming AGREED meetups; resetting the
publisher also cancels its public cards. Already-ended agreements retain their
history without a fabricated cancellation. Finished meeting windows appear in
dashboard history and remain available for feedback during its allowed window.
Historical meetup-confirmation events are read as the actor's existing `met`
feedback without rewriting old records or enabling another submission.

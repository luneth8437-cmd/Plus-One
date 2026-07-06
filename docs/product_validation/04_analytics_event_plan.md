# Analytics Event Plan

Goal: define a minimum event vocabulary for launch testing without collecting private chat content.

This plan describes what should be measured. It does not require adding analytics immediately.

## Privacy Principles

- Do not store raw chat text in analytics.
- Do not store student names, emails, IDs, or exact private contact details.
- Use anonymous session IDs or hashed internal IDs.
- Separate safety/moderation logs from product funnel metrics.
- Keep events small and purpose-bound.

## Core Funnel Events

| Event | When it fires | Key properties | Product question answered |
| --- | --- | --- | --- |
| `visit_discover` | User lands on Discover | session_id, filter_type, visible_card_count | Are users reaching the supply side? |
| `start_create` | User opens Create or focuses the draft input | session_id | Are users starting to publish? |
| `assist_draft` | User requests AI-assisted structuring | session_id, input_length, mode, success, latency_ms | Does AI reduce creation friction? |
| `publish_card` | Card is published | session_id, activity_type, location_id, has_exact_time, expire_minutes | What supply is created? |
| `card_impression` | A card appears in Discover | session_id, post_id, activity_type, age_minutes | Which cards are seen? |
| `pass` | User passes a card | session_id, post_id, activity_type | What supply is rejected? |
| `undo_pass` | User restores the most recent passed card | session_id, post_id | Is pass forgiveness useful? |
| `interested` | User expresses interest in a card | session_id, post_id, activity_type | What drives demand? |
| `match_created` | A one-to-one match is created | post_id, owner_session_id, seeker_session_id, activity_type | Does interest turn into a match? |
| `first_message_sent` | First chat message is accepted | match_id, sender_role, seconds_since_match | Do matches become conversations? |
| `agree_clicked` | Either side agrees to meet | match_id, sender_role, seconds_since_match | Are chats converting to commitment? |
| `handoff_viewed` | Handoff panel is viewed | match_id, viewer_role | Do users reach offline readiness? |
| `decline` | User declines a match | match_id, actor_role, reason_category | Why do matches fail? |
| `report` | User reports content or behavior | match_id, actor_role, reason_category | Where are safety issues? |
| `unsafe_blocked` | Post or message is blocked by moderation | surface, rule_or_model, category, false_positive_reviewed | Is moderation catching risky content? |

## Suggested Funnel

```text
visit_discover
  -> start_create
  -> assist_draft
  -> publish_card
  -> card_impression
  -> interested
  -> match_created
  -> first_message_sent
  -> agree_clicked by both users
  -> handoff_viewed
```

## Minimum Dashboard Metrics

- Published cards per day.
- Card impression to interested rate.
- Interested to match rate.
- Match to first message rate.
- First message to mutual agreement rate.
- Unsafe blocked rate by surface.
- Decline/report rate by match.

## Implementation Notes

- Start with a simple `ProductEvent` model or server-side logging table.
- Prefer server-side events for publish, match, chat, agree, decline, report, and unsafe block.
- Use client-side events only when server-side state cannot capture the action, such as impressions.
- Add retention rules before collecting real campus traffic.

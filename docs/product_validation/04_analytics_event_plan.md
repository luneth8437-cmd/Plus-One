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
| `draft_missing_required_field` | AI draft returns or leaves a required field blank | session_id, field_name, activity_type, draft_strategy | Which AI outputs require user recovery before publish? |
| `date_mismatch_warning_shown` | Review detects a parsed date/time that may conflict with the original text | session_id, field_name, source_text_has_explicit_date, parsed_date | Are date/time guardrails catching risky draft output? |
| `expiry_value_corrected` | Generated expiry is capped or corrected before publish | session_id, original_expire_minutes, corrected_expire_minutes | Are AI-generated numeric values drifting outside product limits? |
| `publish_blocked` | User attempts to publish but validation blocks the card | session_id, blocking_reason, missing_fields, activity_type | Which review blockers prevent broken cards from going live? |
| `publish_card` | Card is published | session_id, activity_type, location_id, has_exact_time, expire_minutes | What supply is created? |
| `card_impression` | A card appears in Discover | session_id, post_id, activity_type, age_minutes | Which cards are seen? |
| `pass` | User passes a card | session_id, post_id, activity_type | What supply is rejected? |
| `undo_pass` | User restores the most recent passed card | session_id, post_id | Is pass forgiveness useful? |
| `interested` | User expresses interest in a card | session_id, post_id, activity_type | What drives demand? |
| `match_created` | A one-to-one match is created | post_id, owner_session_id, seeker_session_id, activity_type | Does interest turn into a match? |
| `open_chat_clicked` | User clicks the Open chat CTA after a match | match_id, viewer_role, seconds_since_match | Does match creation lead clearly into chat? |
| `first_message_sent` | First chat message is accepted | match_id, sender_role, seconds_since_match | Do matches become conversations? |
| `agreement_state_viewed` | User sees the one-sided or two-sided agreement state in chat | match_id, viewer_role, user_agreed, other_agreed | Do users understand mutual agreement before handoff? |
| `agree_clicked` | Either side agrees to meet | match_id, sender_role, seconds_since_match | Are chats converting to commitment? |
| `handoff_viewed` | Handoff panel is viewed | match_id, viewer_role | Do users reach offline readiness? |
| `dashboard_viewed` | User opens My Plus Ones / Dashboard | session_id, source, open_chat_count, handoff_count | Can users find their current state after matching or agreeing? |
| `chat_timer_error` | Chat timer cannot render a valid countdown or terminal state | match_id, timer_state, viewer_role | Are lifecycle states creating broken-looking UI? |
| `decline` | User declines a match | match_id, actor_role, reason_category | Why do matches fail? |
| `report` | User reports content or behavior | match_id, actor_role, reason_category | Where are safety issues? |
| `unsafe_blocked` | Post or message is blocked by moderation | surface, rule_or_model, category, false_positive_reviewed | Is moderation catching risky content? |

## Suggested Funnel

```text
visit_discover
  -> start_create
  -> assist_draft
  -> draft validation events if needed
  -> publish_card
  -> card_impression
  -> interested
  -> match_created
  -> open_chat_clicked
  -> first_message_sent
  -> agreement_state_viewed
  -> agree_clicked by both users
  -> handoff_viewed
  -> dashboard_viewed
```

## Minimum Dashboard Metrics

- Published cards per day.
- Draft missing required field rate.
- Date mismatch warning rate.
- Publish blocked rate by reason.
- Card impression to interested rate.
- Interested to match rate.
- Match to open chat rate.
- Match to first message rate.
- First message to mutual agreement rate.
- Agreement state viewed to second-side agree rate.
- Handoff viewed to Dashboard viewed rate.
- Unsafe blocked rate by surface.
- Decline/report rate by match.
- Chat timer error count.

## Usability-Test-Driven Measurement Questions

These questions come from the 10-session usability report in `03_usability_test_report.md`.

| Finding from usability test | Measurement needed | Related events |
| --- | --- | --- |
| AI draft missed required start time in U1-U5. | How often does AI leave required fields blank, and do users recover? | `assist_draft`, `draft_missing_required_field`, `publish_blocked`, `publish_card` |
| Explicit July 8 text became Jul 7 in U6-U10. | How often do parsed dates conflict with source text? | `assist_draft`, `date_mismatch_warning_shown`, `publish_card` |
| U3 saw 1440-minute expiry and could not publish. | How often do generated numeric values exceed product bounds? | `expiry_value_corrected`, `publish_blocked` |
| U7 had to look for Open chat after matching. | What share of matches actually lead to chat entry? | `match_created`, `open_chat_clicked`, `first_message_sent` |
| U4 had to read carefully to understand mutual agreement. | Does viewing the agreement state lead to second-side agreement? | `agreement_state_viewed`, `agree_clicked`, `handoff_viewed` |
| U5 and U10 hesitated around Dashboard naming. | Can users find Dashboard after agreement? | `handoff_viewed`, `dashboard_viewed` |
| Chat timer showed "--". | Are timer lifecycle errors happening in real sessions? | `chat_timer_error` |

## Implementation Notes

- Start with a simple `ProductEvent` model or server-side logging table.
- Prefer server-side events for publish, match, chat, agree, decline, report, and unsafe block.
- Use client-side events only when server-side state cannot capture the action, such as impressions.
- For draft validation, store field-level status and error categories, not raw natural-language draft text.
- For date mismatch checks, store coarse date relation or normalized date fields, not private text.
- Add retention rules before collecting real campus traffic.

## Implementation Status and First Measured Funnel (2026-07-14)

A first slice of this plan is now implemented as the server-side
`ProductEvent` model: `publish_card`, `match_created`, `opener_suggested`,
`message_sent`, `first_message_sent`, `first_reply_received`, and
`agree_clicked`, plus the `funnel_report` management command. No chat text is
stored; opener adoption is classified (verbatim/edited/none) at send time by
comparing against the AI-generated suggestion texts only.

The first snapshot below comes from **scripted demo traffic**
(`seed_funnel_demo`, 20 sessions driven through the real service layer) and
exists to validate the instrumentation, not to claim real user behavior.

| Funnel step | Count | Conversion |
| --- | --- | --- |
| Cards published | 20 | - |
| Matches created | 17 | 85.0% of cards |
| Matches with first message | 13 | 76.5% of matches |
| Matches with first reply | 11 | 84.6% of first messages |
| Matches with both agreed | 8 | 47.1% of matches |

Opening assistant adoption (scripted mix): 11 suggestion sessions;
6 of 13 first messages used a suggestion (46.2% adoption), half verbatim,
half edited before sending.

Next: replace this snapshot with a small real-user run and add
`opener_clicked`, `decline`, and `report` events from the plan above.

# Product Decision Log

Goal: make the MVP choices explicit so the product reads as intentional, not accidental.

## Decisions

| Decision | Why this is the current choice | Risk | What would change the decision |
| --- | --- | --- | --- |
| Five-minute anonymous chat | Temporary campus plans need quick confirmation. A short timer creates urgency and prevents the product from becoming a general chat app. | Some users may feel rushed. | Usability tests show users need more time before agreeing. |
| Anonymous by default | Lowers the fear of rejection and makes casual plans easier to post. | Trust may be too weak for real campus deployment. | Real launch requires verified student identity or abuse prevention. |
| One-to-one matching | The product promise is finding a Plus One, not hosting a group. It keeps commitment, chat, and handoff simple. | Some activities naturally support groups. | User research shows group activities are the dominant need. |
| Mutual agreement before handoff | Offline meeting is the highest-trust moment. Both users should explicitly choose it. | Extra step may reduce conversion. | Data shows users already understand and want handoff immediately after matching. |
| No permanent profile in MVP | Avoids social-network complexity and reduces identity pressure. | Less trust and weaker repeat-user personalization. | Verified campus launch needs reputation, preferences, or friend context. |
| No group matching in MVP | Keeps moderation, chat, capacity, and UI simple. | Limits sports and study groups. | Interviews show group formation is more important than one-to-one pairing. |
| AI assists but does not auto-publish | AI reduces writing effort, but users remain responsible for reviewing the public card. | Users may ignore review fields. | AI accuracy becomes very high and publishing still includes safety review. |
| Rule fallback remains required | Demo, local development, and low-cost deployment must work even without API keys. | Fallback quality is lower than LLM output. | The product is deployed only in environments with reliable model access. |

## Open Questions

- Should anonymous sessions eventually become verified student sessions?
- Should chat expiry be exactly five minutes, or should users be able to extend once?
- Should the product show how many people have passed or shown interest?
- Should safety reporting create an admin review queue?
- Should location be restricted to verified campus places only?

## Decision Review Cadence

Revisit these choices after:

- 5-8 discovery interviews.
- 5-10 usability tests.
- First live demo feedback round.
- First analytics event implementation.
- First real moderation false positive or false negative review.

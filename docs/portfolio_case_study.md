# Plus One Portfolio Case Study

## Summary

Plus One is an AI-assisted anonymous campus matching product for finding one person to join a temporary activity: lunch, study, sports, coffee, campus events, or short breaks between classes.

The product is designed around a narrow problem: students often want company for a low-commitment activity, but existing channels make the request feel too public, too slow, or too socially heavy. Plus One turns a casual sentence into a temporary card, lets another anonymous student show interest, opens a five-minute one-to-one chat, and reveals meeting details only after both people agree.

Live demo: https://plusone-ub3w.onrender.com/

GitHub: https://github.com/luneth8437-cmd/Plus-One

## My Role

- Product strategy and scope definition.
- Interaction design for discovery, matching, anonymous chat, and handoff.
- Django full-stack implementation.
- DeepSeek integration for drafting, icebreakers, and safety moderation.
- Deployment, documentation, validation pack, and demo materials.

## Problem

Campus companion-finding has three recurring uncertainties:

1. Availability: Who is free right now or soon?
2. Intent fit: Does the other person want the same activity in the same way?
3. Safety and comfort: Is it safe and socially comfortable enough to meet offline?

Existing options solve parts of the problem but not the full moment:

- Group chats are noisy and make casual asks feel public.
- Direct messages only work inside existing friend circles.
- Social stories are passive and weakly tied to immediate intent.
- General social or dating products carry profile pressure and the wrong context.

## Product Positioning

Plus One is not a permanent social network. It is a short-lived campus matching tool:

- Anonymous first contact.
- Temporary activity cards.
- One-to-one matching.
- Five-minute chat.
- Mutual agreement before meeting details.
- Safety checks before posts and messages are accepted.

The product intentionally avoids permanent profiles, group matching, and open-ended chat in the MVP.

## Target Scenarios

The validation pack currently models five target contexts:

| Scenario | Core need | Product implication |
| --- | --- | --- |
| Meal companion | Quick, low-pressure company | Keep cards casual and short-lived. |
| Study companion | Topic, focus mode, quiet location | Capture study topic and expected duration. |
| Sports companion | Skill level and one-person fit | Clarify that the product finds one companion. |
| Commuter short gap | Fast decision within a limited time window | Prioritize recency, location, and time-left clarity. |
| Language practice or coffee | Low-pressure practice and graceful exit | Preserve anonymous chat, Decline, and Report separation. |

These are role-play validation contexts, not completed real student interviews. Real interviews are the next validation step.

## Core Flow

```text
Create card
  -> AI drafts structured fields
  -> User reviews and publishes
  -> Another anonymous session discovers the card
  -> Interested creates one-to-one match
  -> Five-minute anonymous chat opens
  -> Both users Agree
  -> Meeting details appear
  -> Dashboard tracks live cards, open chats, handoffs, and closed activity
```

## Key Product Decisions

| Decision | Reason | Tradeoff |
| --- | --- | --- |
| Anonymous by default | Reduces rejection pressure and makes casual asks easier. | Trust may need verified student identity before campus-scale launch. |
| One-to-one matching | Matches the "find a Plus One" promise and simplifies commitment. | Some team sports or group events may need separate flows later. |
| Five-minute chat | Keeps temporary plans moving and avoids turning the product into general chat. | Some users may want one extension. |
| Mutual Agree before meeting details | The offline handoff is the highest-trust moment. | Adds one conversion step. |
| AI assists but does not auto-publish | AI reduces writing effort, but users must review public card details. | Requires one extra review step. |
| Rule fallback remains | Demo and core flow work even if external model calls fail. | Fallback is less flexible than LLM output. |

## AI System

AI is used where it lowers friction or improves safety:

- Natural-language post drafting.
- Icebreaker generation.
- Post and chat moderation.

Reliability choices:

- DeepSeek is called through an OpenAI-compatible client.
- If no provider is available or a model call fails, deterministic fallback keeps the product usable.
- `LLMLog` records model/fallback calls for debugging and auditability.
- Safety moderation merges model output with local rules so local rules remain a hard safety floor.

## Validation Evidence

### Product-Flow Role-Play

Five role-play contexts were run through the actual Django server-side flow with a temporary SQLite database:

```text
Create -> Assist -> Publish -> Discover -> Interested -> Chat -> Agree -> Dashboard
```

Result:

- 5/5 cards published.
- 5/5 cards visible to a second anonymous identity.
- 5/5 matches created from Interested.
- 5/5 chats opened by both sides.
- 5/5 first-message exchanges completed.
- 5/5 flows reached mutual-agreement handoff.

This is execution evidence for the current product path, not real human usability evidence.

### AI Evaluation

Baseline rule fallback:

```text
Activity type accuracy: 14/15
Location accuracy: 15/15
Safety accuracy: 5/5
```

Runtime DeepSeek role-play evaluation:

```text
Provider: DeepSeek
Model: deepseek-v4-flash
Parsing activity type: 3/5
Parsing location: 4/5
Clear clock time handling: 5/5
Safety moderation: 5/5
```

Interpretation:

- Safety moderation was strong in the small role-play set.
- Parsing needs more guardrails for mixed-intent cards, such as coffee plus commute or language practice plus coffee.
- Manual review before publish remains necessary.

## Iteration Examples

| Issue | Evidence | Change |
| --- | --- | --- |
| Raw AI/fallback output hurt the create experience. | Manual testing showed debug-like output below the form. | Removed raw output from the user-facing flow. |
| Ambiguous time was silently interpreted. | Inputs like "tomorrow at 7" could become an unintended AM/PM choice. | Added ambiguity handling so users choose morning or evening. |
| Dashboard states were hard to scan. | Active posts, open chats, handoffs, and history were mixed. | Reworked Dashboard into state-based panels. |
| Decline and Report were different intentions. | Normal rejection should not feel like a safety report. | Kept Decline and Report separate. |
| Live cards could become visually unstable. | Long content created irregular dashboard cards. | Stabilized card layout and removed group-capacity language. |
| One-to-one model was diluted by "People needed". | The product promise is "find a Plus One", not group matching. | Removed user-facing multi-person capacity from the create flow. |

## Current Outcome

Plus One now has:

- A working deployed demo.
- A full two-person matching and chat flow.
- AI-assisted creation and moderation.
- Rule fallback and LLM logging.
- Product validation documentation.
- README screenshots and a two-person GIF.
- Deployment and security docs.

## Open Risks

- Role-play validation is not a substitute for recruited student research.
- DeepSeek parsing still misclassifies mixed-intent cards.
- The word "handoff" may be too internal for user-facing copy.
- Real campus launch likely needs verified student identity or stronger abuse prevention.
- Event analytics are defined but not yet implemented.

## Next Steps

1. Recruit 5-8 real students for interviews.
2. Run 5-10 usability tests with the live demo.
3. Rename user-facing handoff copy to "meeting details" if real users confirm confusion.
4. Add parser guardrails for mixed-intent activities.
5. Implement the minimum analytics event plan.
6. Update this case study with real user evidence after testing.

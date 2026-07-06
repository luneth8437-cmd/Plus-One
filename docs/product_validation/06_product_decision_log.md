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

## Interview-Backed Product Decisions

These decisions come from eight anonymized student interviews recorded in `01_user_interviews.md`.

| Decision | Interview evidence | Chosen direction | Tradeoff | Future revisit condition |
| --- | --- | --- | --- | --- |
| First-screen positioning must be activity-first | Users repeatedly distinguished Plus One from dating and social networking. P1 said they were not trying to make friends; P2 said the product works if it emphasizes activity rather than profile. | Lead with "one student for one campus plan" instead of profiles, bios, or social feed language. Suggested copy: "Find one student for one campus plan - lunch, study, coffee, sports, or language practice. No profiles. No follower graph. Just a short plan and a quick vibe check." | Activity-first positioning may feel less emotionally rich than a social app. | If later research shows users want ongoing relationships or repeated companion history, revisit profile depth. |
| Card templates must make the plan specific | Users rejected vague cards such as "hang out" and asked for time, place, duration, skill level, study mode, or meeting point. | Prompt users toward specific cards such as "Coffee near campus in 20 minutes", "Quiet study at library for 2 hours", "Badminton at 18:00, intermediate level", and "Lunch at Mensa after class". | More structure can make posting feel heavier. | If create completion drops, reduce required fields but keep smart examples and AI drafting. |
| Safety must be productized, not only documented | Safety-sensitive users accepted anonymity only with visible boundaries: school-only community, report, decline, chat expiry, no automatic contact exchange, and public-place handoff. | Make safety cues visible in the flow: anonymous until both agree, meet in public campus spaces, report/decline available, chat expires automatically, no contact exchange required. | More safety copy can add friction or make the product feel risky. | If users report confusion or unsafe behavior, introduce verified student mode and an admin review queue earlier. |
| Cold start needs explicit empty-state design | P2 and P8 said an empty Discover queue would make them close the product or feel the product is useless. | Empty state should invite creation and set expectations: "No active plans right now. Create a quick card for lunch, study, coffee, sports, or language practice - most plans are meant for the next 30-120 minutes." | Showing activity signals too early can mislead if the supply is still low. | Once analytics exists, revisit based on card impression count, create-start rate from empty state, and return visits. |
| AI-assisted creation is a pressure-reduction feature | P3 and P8 said AI draft helps turn awkward intent into a clear invitation. | Position AI as helping users express a low-pressure campus plan, not as a novelty or autonomous publisher. Product meaning: "Helping users turn awkward intent into a clear, low-pressure campus invitation." | AI can misparse time, location, or intent. | Keep manual review before publishing until AI evaluation shows consistently high accuracy. |

## Activity-Specific Card Structure

The interview synthesis showed that "specific enough to trust" differs by activity type. The MVP can keep one simple card model, but the create flow and AI draft should nudge users toward these details.

| Activity type | Information users need before tapping Interested | Product implication |
| --- | --- | --- |
| Lunch / Coffee | Time, location, expected duration | Examples and AI output should avoid vague "hang out" phrasing and include a concrete campus place. |
| Study | Location, start time, duration, quiet study vs discussion | Study cards should make intent clear so users do not worry the other person wants casual chatting. |
| Sports | Sport type, place, time, skill level, court/equipment status | Sports cards need practical details because the chat is mainly for confirmation. |
| Language practice | Language pair, time, place, practice mode | Position as low-pressure practice, not dating or open-ended socializing. |
| Campus event | Event name, meeting point, start time, whether to enter together | Keep the product as companion-finding, not full event management. |

## Open Questions

- Should anonymous sessions eventually become verified student sessions?
- Should chat expiry be exactly five minutes, or should users be able to extend once?
- Should the product show how many people have passed or shown interest?
- Should safety reporting create an admin review queue?
- Should location be restricted to verified campus places only?
- How should the product seed enough visible supply during the first campus launch?
- How much safety reassurance should appear in the UI before it starts making the product feel risky?

## Decision Review Cadence

Revisit these choices after:

- 5-8 discovery interviews. Completed first pass: eight anonymized student interviews added on 2026-07-06.
- 5-10 usability tests.
- First live demo feedback round.
- First analytics event implementation.
- First real moderation false positive or false negative review.

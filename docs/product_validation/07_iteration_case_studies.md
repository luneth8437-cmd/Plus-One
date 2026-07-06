# Iteration Case Studies

Goal: show that Plus One improved, and should keep improving, through observed evidence rather than feature accumulation.

Format:

```text
Problem -> Evidence -> Solution -> Validation
```

Evidence sources:

- `01_user_interviews.md`: eight anonymized student interviews and interview evidence synthesis.
- `03_usability_test_report.md`: 10-session usability report for the full create-match-chat-agree-handoff-dashboard loop.

## Case 1: AI Draft Review Must Be Defensive

Problem:

- AI-assisted creation can make posting easier, but users may trust incorrect structured fields.

Evidence:

- U1-U5 needed manual time fixes after casual-text drafting.
- U3 received a draft with missing start time and a 1440-minute expiry, then publish stayed on the create form.
- U6-U10 used explicit date/time, but July 8 became Jul 7 in the structured card.
- U6 said: "This one filled the time, so I trust the draft more."

Solution:

- Planned: highlight missing required fields after draft.
- Planned: validate parsed date/time against the user's original text.
- Planned: cap generated expiry values.
- Planned: block Publish with a clear inline message until the card is valid.

Validation:

- Current evidence: usability report identified this as the highest-impact fix.
- Next validation: rerun create-task usability sessions and measure whether users can publish accurate cards without facilitator help.

## Case 2: Interested Should Lead More Directly To Chat

Problem:

- After a user taps Interested, the match is created but the next step is not visually strong enough.

Evidence:

- U7 completed the match but said: "The match happened, but I had to look for the chat button."
- Task-level observation: Interested created a match, but the user remained on Discover with an Open chat CTA.

Solution:

- Planned: make Open chat the primary next step after a match.
- Option to test: auto-open chat after successful match creation.
- If auto-open feels too abrupt, make a sticky Open chat action visually dominant.

Validation:

- Measure `match_created -> open_chat_clicked` conversion.
- In usability retest, ask whether users know what to do immediately after Interested.

## Case 3: Mutual Agreement Needs A Two-Person Checklist

Problem:

- The product requires both users to agree before handoff, but the one-sided agreement state is too text-heavy.

Evidence:

- U4 said: "I agreed, but it says the other person has not agreed, so we are not done yet."
- Task-level observation: one-sided agreement displays "You agreed: yes / Match agreed: no", which users must read carefully.

Solution:

- Planned: show a two-person agreement checklist:
  - You agreed.
  - Other person agreed.
  - Handoff ready.

Validation:

- Measure `agree_clicked` by first user to second-user agreement completion.
- In usability retest, ask users to explain whether the meeting is ready after only one side agrees.

## Case 4: Dashboard Naming Should Match User Tasks

Problem:

- Users can find the state page, but the navigation label `My Plus Ones` does not match the task language `Dashboard`.

Evidence:

- U5 said: "Where is Dashboard? Oh, maybe My Plus Ones is the dashboard."
- U10 said: "Ready to meet is clear once I find My Plus Ones."

Solution:

- Planned: use Dashboard wording in the nav or page header.
- Candidate label: `My Plus Ones / Dashboard`.
- Keep the product meaning of "My Plus Ones" but make the functional destination obvious.

Validation:

- Measure dashboard visits after mutual agreement.
- In usability retest, ask users where they would go to check ready-to-meet plans.

## Case 5: Chat Timer Must Not Look Broken

Problem:

- A five-minute chat timer supports the product concept, but a broken-looking timer lowers confidence.

Evidence:

- Task-level observation: messages appeared in anonymous chat, but the chat timer displayed "--" during the run.
- Observation checklist: the timer showing "--" looked broken and reduced confidence.

Solution:

- Planned: ensure the chat timer always shows a valid countdown or a clear expired/ready state.
- Add a fallback label only when the match has no active countdown.

Validation:

- Add a visual/manual checklist for active chat, expired chat, and agreed handoff states.
- In usability retest, ask whether the timer feels helpful, stressful, or broken.

## Case 6: Pass And Interested Need Clearer First-Use Meaning

Problem:

- The heart for Interested is understandable, but an icon-only x can be read as close/delete instead of Pass.

Evidence:

- U8 said: "The heart is clear; the x still feels like close or delete."
- Observation checklist: Interested and Pass were only partially clear.

Solution:

- Planned: add text labels or first-use tooltips for Pass and Interested.
- Keep the fast decision model, but reduce ambiguity for new users.

Validation:

- Observe whether first-time users can explain both controls before tapping them.
- Measure pass/undo rate after label changes.

## Case 7: Safety Copy Should Stay Concise And Visible

Problem:

- Anonymous meetups create mild concern, but heavy safety copy could make the product feel risky.

Evidence:

- Success metrics: 0 critical safety confusion cases; 5/10 had mild concerns about anonymous meetups.
- U9 said: "The public-place reminder makes the meetup feel less sketchy."
- Interviews showed users accept anonymity only when there are clear boundaries.

Solution:

- Keep concise safety copy in chat and handoff.
- Keep Decline and Report safety issue visibly separate.
- Future launch consideration: verified student identity without forcing public profiles.

Validation:

- Track report/decline use separately.
- In future tests, ask whether safety copy feels reassuring or alarming.

## Case 8: One-to-One Positioning Must Stay Explicit

Problem:

- Some activities, especially sports, can create group expectations, but Plus One is intentionally one-to-one.

Evidence:

- Interviews showed users valued one companion for meals, study, coffee, language practice, and campus events.
- The usability report found that users understood the broad concept, but Discover still felt like a swipe queue to U1.

Solution:

- Keep one-line product promise visible: one student, one campus plan.
- Avoid capacity language such as `people needed`.
- Use activity examples that reinforce one companion rather than group formation.

Validation:

- In future tests, ask what users think happens after tapping Interested.
- Watch whether sports users expect a group game or one companion.

## Portfolio Summary

Strongest iteration story:

> The full Plus One loop works for most users, but the biggest risk is not matching or chat. It is whether users can trust AI-generated structured cards before publishing. The next product iteration should make the review step defensive, not merely editable.

Evidence-backed next product priorities:

1. Defensive AI draft review.
2. Stronger Interested-to-chat transition.
3. Two-person agreement checklist.
4. Dashboard naming clarity.
5. Reliable chat timer.
6. First-use Pass/Interested labels.
7. Concise safety copy.

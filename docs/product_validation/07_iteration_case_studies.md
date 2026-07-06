# Iteration Case Studies

Goal: show that Plus One improved through observed problems, not just feature accumulation.

Use this format for portfolio or README evidence:

```text
Problem -> Evidence -> Solution -> Validation
```

## Case 1: Pass Needed Recovery

Problem:

- Passing a card was too final for a fast, swipe-like interface.

Evidence:

- Internal product walkthroughs showed that accidental pass actions created avoidable dead ends.

Solution:

- Added an undo path for the most recent skipped active card.

Validation:

- Automated tests cover pass and undo behavior.
- Real user validation still needed.

## Case 2: Dashboard Needed State Clarity

Problem:

- Active cards, open chats, handoffs, and closed activity were easy to confuse when shown as generic lists.

Evidence:

- Product walkthroughs found that users needed to know what required action now versus what was only history.

Solution:

- Reworked Dashboard into state-based panels: open decisions, live cards, meet handoffs, and closed activity.

Validation:

- Dashboard status tests pass.
- Real usability test should ask users what they would do next from each panel.

## Case 3: Decline and Report Needed Separation

Problem:

- Ending a chat and reporting unsafe behavior are different user intentions.

Evidence:

- Product review found that combining them would make normal rejection feel too severe and unsafe reports too hidden.

Solution:

- Kept decline as a normal match decision and report as a safety action.

Validation:

- Permission and moderation tests pass.
- Real users should be asked whether the distinction is clear.

## Case 4: AI Language Needed User-Facing Copy

Problem:

- Showing terms like `LLMLog`, raw fallback output, or model-shaped JSON made the product feel like a debug tool.

Evidence:

- Manual testing showed raw AI/fallback output appearing directly below the create form.

Solution:

- Removed raw output from the main user flow and replaced technical copy with product language.

Validation:

- Create flow now focuses on drafting, review, and publish.
- Real users should be asked whether AI assistance is understandable without technical explanation.

## Case 5: Live Cards Needed Layout Stability

Problem:

- Live card content could collapse into narrow, irregular shapes, making the Dashboard look broken.

Evidence:

- Manual testing produced a live card with title and metadata wrapping into unusable columns.

Solution:

- Stabilized Dashboard card layout and removed mismatched capacity language from the one-to-one product model.

Validation:

- Manual visual review passed after the layout change.
- Add a screenshot test or visual checklist for long titles before launch.

## Case 6: Time Ambiguity Needed Confirmation

Problem:

- Inputs such as "tomorrow at 7" could be interpreted as morning or evening without user intent.

Evidence:

- Manual tests showed the review form picking a time even when AM/PM was not specified.

Solution:

- Added ambiguity handling so users can confirm morning or evening instead of silently accepting an assumption.

Validation:

- Tests cover ambiguous time handling.
- Real usability tests should check whether users notice and understand the confirmation.

## Role-Play Feedback Evidence

Date: 2026-07-06

Source: five role-play product flows plus runtime AI evaluation. This is not real student evidence, but it is useful as a pre-test backlog.

| Finding | Evidence | Product implication |
| --- | --- | --- |
| The full create-match-chat-agree-dashboard path works for all five target contexts. | 5/5 role-play flows reached `agreed` match status and opened Dashboard successfully. | Keep the current core flow stable while improving copy and edge cases. |
| One-to-one positioning must stay explicit. | Sports role-play fit tennis/light practice but raised group-sport expectation risk. | Avoid adding `people needed`; reinforce "find one companion." |
| `handoff` may be too internal as user-facing language. | Language/coffee role-play expected "meeting details" after both users agree. | Consider renaming public copy while keeping internal model names unchanged. |
| Mixed-intent AI parsing needs guardrails. | DeepSeek classified coffee before bus as `explore`, and language practice over coffee as `other` at `Student Center`. | Add post-processing or prompt examples for coffee, commute, and language practice. |
| Safety moderation is currently stronger than parsing in the small role-play run. | 5/5 safety samples matched expected allow/block behavior; parsing was 3/5 for activity and 4/5 for location. | Prioritize parser quality before expanding AI automation. |

## Next Cases To Add

- Safety blocked example from real moderation testing.
- First live user feedback case.
- Analytics-driven funnel improvement after event tracking exists.

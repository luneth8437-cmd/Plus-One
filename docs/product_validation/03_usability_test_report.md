# Usability Test Report

Goal: verify whether new users can complete the full Plus One flow without explanation.

Recommended sample: 5-10 students. Use the live Render demo or a clean local server. Test with two browser sessions so one person can experience both sides of the match.

## Test Tasks

1. Open Discover and understand what the product is for.
2. Create a temporary Plus One card from casual text.
3. Review and publish the structured card.
4. Switch to a second anonymous identity.
5. Find the card in Discover and tap Interested.
6. Enter the anonymous chat.
7. Send the first message.
8. Make both sides agree to meet.
9. View the handoff.
10. Check the Dashboard state.

## Success Metrics

| Metric | Target | Result |
| --- | --- | --- |
| Create card completion | 80%+ | TBD |
| Match creation completion | 80%+ | TBD |
| First message sent | 80%+ | TBD |
| Handoff viewed | 70%+ | TBD |
| No explanation needed for primary flow | 70%+ | TBD |
| Critical safety confusion | 0 cases | TBD |

## Participant Results

Replace placeholders with real observations.

| Participant | Create completed | Match completed | Chat completed | Handoff viewed | Main friction | Quote | Change made or planned |
| --- | --- | --- | --- | --- | --- | --- | --- |
| U1 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| U2 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| U3 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| U4 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| U5 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| U6 | Optional | TBD | TBD | TBD | TBD | TBD | TBD |
| U7 | Optional | TBD | TBD | TBD | TBD | TBD | TBD |
| U8 | Optional | TBD | TBD | TBD | TBD | TBD | TBD |
| U9 | Optional | TBD | TBD | TBD | TBD | TBD | TBD |
| U10 | Optional | TBD | TBD | TBD | TBD | TBD | TBD |

## Simulated Pilot Results

These results are synthetic. They are useful for a dry run before recruiting real students, but they must not be presented as real usability evidence.

### Simulated Metrics

| Metric | Target | Simulated result | Interpretation |
| --- | --- | --- | --- |
| Create card completion | 80%+ | 5/5 | The create flow appears learnable when users understand the casual text input. |
| Match creation completion | 80%+ | 4/5 | One simulated user hesitated because the second anonymous identity concept felt like a test-only artifact. |
| First message sent | 80%+ | 5/5 | Chat entry and first message are clear enough in the dry run. |
| Handoff viewed | 70%+ | 4/5 | One simulated user did not understand the word handoff without context. |
| No explanation needed for primary flow | 70%+ | 3/5 | The core idea is understandable, but anonymous session mechanics need clearer framing. |
| Critical safety confusion | 0 cases | 0/5 | Decline and report stayed conceptually separate in the dry run. |

### Simulated Participants

| Simulated user | Create completed | Match completed | Chat completed | Handoff viewed | Main friction | Simulated quote | Change made or planned |
| --- | --- | --- | --- | --- | --- | --- | --- |
| U1 | Yes | Yes | Yes | Yes | Needed a moment to understand why the card disappears after matching | "Once I saw it was one-to-one, the disappearing card made sense." | Keep one-to-one positioning visible in README and product copy. |
| U2 | Yes | Yes | Yes | Yes | Unsure whether "tomorrow at 7" would mean morning or evening | "I would want the app to ask me if it is 7 AM or 7 PM." | Preserve ambiguity confirmation and test it with real users. |
| U3 | Yes | Partial | Yes | No | Did not understand the word "handoff" quickly | "Handoff sounds internal; I expected something like meeting details." | Consider renaming handoff copy to "meeting details" in user-facing UI. |
| U4 | Yes | Yes | Yes | Yes | Five-minute timer felt useful but slightly stressful | "The timer makes me decide, but I might want one quick extension." | Consider a one-time extension only if real users also ask for it. |
| U5 | Yes | Yes | Yes | Yes | Wanted stronger reassurance that report is for safety and decline is normal | "I like that saying no is not the same as reporting someone." | Keep Decline and Report visually distinct. |

### Simulated Findings

1. The two-person value proposition is clear after the first match, but less clear before the first match.
2. Time ambiguity remains one of the highest-risk moments in the create flow.
3. "Handoff" is precise internally but may not be the best user-facing label.
4. The five-minute chat timer supports urgency, but real users may ask for one extension.
5. Decline and Report separation is important because it makes normal rejection feel acceptable.

### Simulated Fix Backlog

| Priority | Candidate fix | Why it matters | Validation needed |
| --- | --- | --- | --- |
| P1 | Rename user-facing handoff copy to "meeting details" | Reduces jargon at the most important conversion moment | Ask real users what they expect after both agree |
| P1 | Add activity-specific create examples | Helps users produce better cards faster | Measure create completion and editing effort |
| P2 | Add clearer one-to-one positioning in empty states and onboarding copy | Prevents confusion about group matching | Ask users what "Plus One" means before testing |
| P2 | Add timer warning near final minute | Reduces stress without removing urgency | Observe chat completion and agreement rate |
| P3 | Explore verified student mode | Improves trust for real campus launch | Validate in interviews before implementation |

## Observation Checklist

- Did the user understand that cards are temporary?
- Did the user understand anonymous sessions?
- Did the user know why a second identity is needed for testing?
- Did `Interested`, `Pass`, and undo feel clear?
- Did the chat expiry feel helpful or stressful?
- Did both users understand that handoff requires mutual agreement?
- Did any UI element look broken, crowded, or unclear on the test device?
- Did any safety warning feel too weak, too strong, or confusing?

## Synthesis

Top usability issues:

1. TBD
2. TBD
3. TBD

Highest-impact fix:

- TBD

Evidence to add to portfolio:

- TBD

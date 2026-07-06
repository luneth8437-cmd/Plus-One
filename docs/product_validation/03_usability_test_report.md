# Usability Test Report

Goal: verify whether new users can complete the full Plus One flow without explanation, and identify the highest-risk usability issues before broader launch testing.

Recommended sample: 5-10 students. This report records a 10-session usability test against the full create-match-chat-agree-handoff-dashboard loop.

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
| Create card completion | 80%+ | 9/10, 90%. Meets flow target, but content accuracy is not safe: U1-U5 exposed missing start time, and U6-U10 exposed one-day date shift from July 8 to Jul 7. |
| Match creation completion | 80%+ | 9/10, 90%. Meets target among published cards. |
| First message sent | 80%+ | 9/10, 90%. Meets target among published cards. |
| Handoff viewed | 70%+ | 9/10, 90%. Meets target after both sides agreed. |
| No explanation needed for primary flow | 70%+ | 6/10, 60%. Below target. Main blockers were missing/incorrect draft fields, Interested-to-chat transition, agreement state, and Dashboard naming. |
| Critical safety confusion | 0 cases | 0 critical cases. 5/10 had mild concerns about anonymous meetups, but Report/Decline and public-place guidance were visible. |

Additional signal: full casual-text draft reliability is still weak. U1-U5 used vaguer casual text and 5/5 drafts missed start time. U6-U10 used explicit date/time and 5/5 drafts filled required fields, but all five shifted the requested July 8 date to Jul 7 in the published card.

## Participant Results

| Participant | Create completed | Match completed | Chat completed | Handoff viewed | Main friction | Quote | Change made or planned |
| --- | --- | --- | --- | --- | --- | --- | --- |
| U1 | Yes, after manual time fix | Yes | Yes | Yes | Discover explains the concept, but the first screen still feels like a swipe queue. | "I think this is for finding one person for a campus plan, but I had to read the small paragraph." | Planned: add a one-line product promise above the queue. |
| U2 | Yes, after manual time fix | Yes | Yes | Yes | Casual draft omitted start time, so the user had to notice and complete a required field. | "It drafted the card, but why is the time blank?" | Planned: highlight missing required fields after draft and suggest a default time. |
| U3 | No | No | No | No | Draft omitted start time and generated a 1440-minute expiry; publish stayed on the create form instead of completing. | "It made the workout card, but publishing just keeps me here." | Planned: cap draft-generated expiry values and show a clear inline publish blocker. |
| U4 | Yes, after manual time fix | Yes | Yes | Yes | Mutual agreement requirement is clear only after reading chat status text. | "I agreed, but it says the other person has not agreed, so we are not done yet." | Planned: show a two-person agreement checklist in chat. |
| U5 | Yes, after manual time fix | Yes | Yes | Yes | Dashboard state is findable, but navigation says My Plus Ones, not Dashboard. | "Where is Dashboard? Oh, maybe My Plus Ones is the dashboard." | Planned: rename or pair the label as My Plus Ones / Dashboard. |
| U6 | Yes, but date shifted | Yes | Yes | Yes | Explicit time filled required fields, but July 8 became Jul 7 in the structured card. | "This one filled the time, so I trust the draft more." | Planned: validate parsed date against the user's text and show a date-confirmation warning. |
| U7 | Yes, but date shifted | Yes | Yes | Yes | Discover card was findable, but the Interested result still required noticing Open chat. | "The match happened, but I had to look for the chat button." | Planned: make Open chat the primary next step after a match. |
| U8 | Yes, but date shifted | Yes | Yes | Yes | Pass/Interested icon meaning was understandable only after seeing the card context. | "The heart is clear; the x still feels like close or delete." | Planned: add text labels or first-use tooltips for Pass and Interested. |
| U9 | Yes, but date shifted | Yes | Yes | Yes | Safety guidance in the handoff was useful and did not block completion. | "The public-place reminder makes the meetup feel less sketchy." | Planned: keep concise safety copy in chat and handoff. |
| U10 | Yes, but date shifted | Yes | Yes | Yes | Dashboard was correct after mutual agreement, but Dashboard naming remained a small hesitation. | "Ready to meet is clear once I find My Plus Ones." | Planned: use Dashboard wording in nav or page header. |

## Task-Level Observations

| Task | Result | Evidence |
| --- | --- | --- |
| Understand Discover | 10/10 understood the broad idea. | The page says "Pick one plan. Decide fast." and explains temporary identity and five-minute anonymous chat. Users still needed to read the paragraph, not just scan the UI. |
| Create from casual text | 9/10 published, but only with serious review caveats. | U1-U5 missed start time. U3 failed publish. U6-U10 filled time but shifted requested July 8 to Jul 7. |
| Review and publish | 9/10 completed. | Cards showed "Your card is live" after publish. However, U6-U10 would have published the wrong date if the user trusted the draft. |
| Switch to second identity | 10/10 technically completed. | Separate anonymous sessions worked. The product explains anonymous sessions, but test users need to be told why a second identity is required for testing. |
| Find card and tap Interested | 9/10 completed. | Published cards appeared in Discover for the second identity. Interested created a match, but the user remained on Discover with an Open chat CTA. |
| Enter chat | 9/10 completed. | Open chat was available after match creation. The transition is not automatic. |
| Send first message | 9/10 completed. | Messages appeared in the anonymous chat. The chat timer displayed "--" during the run, which looked unfinished. |
| Both sides agree | 9/10 completed. | One-sided agreement shows "You agreed: yes / Match agreed: no." After the creator also agreed, chat changed to "Status: Agreed to meet" and "Meet handoff ready." |
| View handoff | 9/10 completed. | The handoff view included plan, meet location, time, and safety check. |
| Check Dashboard state | 9/10 completed. | Dashboard showed "Ready to meet" and "View handoff" after mutual agreement. The nav label is "My Plus Ones," which caused naming hesitation. |

## Observation Checklist

| Question | Observation |
| --- | --- |
| Did the user understand that cards are temporary? | Partially. Expiry language is present, but users focused more on the activity card than the temporary nature. |
| Did the user understand anonymous sessions? | Mostly. The session page explains anonymity well, but the Discover flow relies on small "Anonymous session" navigation text. |
| Did the user know why a second identity is needed for testing? | No. This is a test setup concept, not a product concept. It needs facilitator explanation during usability testing. |
| Did `Interested`, `Pass`, and undo feel clear? | Partially. Interested is understandable as a heart, but Pass uses an icon-only x. Undo after Pass is helpful. |
| Did the chat expiry feel helpful or stressful? | Mixed. The five-minute framing supports fast decisions, but the timer showed "--", which looked broken and reduced confidence. |
| Did both users understand that handoff requires mutual agreement? | Partially. After both agree, the handoff state is clear. Before both agree, users need to read "Match agreed: no" carefully. |
| Did any UI element look broken, crowded, or unclear on the test device? | The chat timer showing "--" looked broken. The create form allowed draft output with missing required time, and explicit July 8 text was converted to Jul 7 in U6-U10. |
| Did any safety warning feel too weak, too strong, or confusing? | No critical confusion. Mild concern remained around anonymous meetups. Decline, Report safety issue, and public-place handoff guidance were useful. |

## Synthesis

Top usability issues:

1. Casual-text drafting is not reliable enough for publish-ready cards.
   Evidence: U1-U5 missed start time; U3 failed publish after the draft generated a 1440-minute expiry; U6-U10 filled required fields but shifted explicit July 8 requests to Jul 7. Users can recover only if they notice the missing or incorrect structured fields.

2. Match-to-chat and agreement state need stronger guidance.
   Evidence: Interested creates a match but leaves the user on Discover. One-sided Agree says "You agreed: yes" while the match remains incomplete until the other person agrees.

3. Dashboard discoverability is weaker than the task language.
   Evidence: the task says Dashboard, but navigation says My Plus Ones. Users can find it, but with hesitation.

Highest-impact fix:

- Make the post-draft review step defensive: highlight missing required fields, validate parsed date/time against the user's text, suggest a default start time only when confidence is low, cap generated expiry values, and block Publish with a clear inline message until the structured card is valid. This would likely move create completion from 9/10 flow-complete to genuinely reliable without facilitator help.

Secondary fixes:

- After Interested, either auto-open chat or make Open chat sticky and visually dominant.
- Replace or supplement icon-only Pass and Interested controls with text labels on first use.
- Show a two-person agreement checklist in chat: You agreed / Other person agreed / Handoff ready.
- Rename the navigation label to Dashboard or My Plus Ones / Dashboard.
- Fix the chat timer so it shows an actual countdown instead of "--".

Evidence to add to portfolio:

- 10-session usability metric table showing 9/10 full handoff completion and a major draft-accuracy risk.
- Before/after screenshots for Draft review validation, Interested-to-chat transition, and mutual-agreement checklist.
- Quote set showing user confusion around missing time, Open chat, and Dashboard naming.
- Concise product learning: "The full Plus One loop works, but first-time success depends on the review step making missing or incorrect structure impossible to miss."

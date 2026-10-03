# Meeting interactions

This change focuses on the waiting room, the five-minute conversation, and
following through on a real plan. It does not create sample public activities
or claim that another participant is online.

## Participant flow

The waiting room hides message entry and AI suggestions. Recent foreground
page signals start the existing five-minute clock only when both participants
are present. The page distinguishes navigating away from explicitly exiting
the match. The existing ten-minute/card-expiry waiting bound remains.

During conversation, participants can review and edit an exact public meeting
point, meeting time, and expected end. A changed plan clears both confirmations.
Both must confirm the same revision. Editing never resets the chat deadline.
Unsaved edits are retained when a newer plan arrives; the participant chooses
whether to use the latest details or propose those edits.

After agreement, free chat closes. The saved handoff offers arrival and delay
signals near the meeting, cancellation, and personal meetup feedback. Five- or
ten-minute delay signals leave the confirmed time unchanged. Signals update
when the other person views the plan; no push notification is promised.

Cancellation clearly tells both participants not to travel under the old
arrangement. Recruitment stays paused until the publisher explicitly reopens
the still-valid original card. Reporting an agreed meetup also cancels it.
The agreement and reporting history remain available. Arrival is not evidence
that people met, and one participant's feedback does not set the other's.

Plans are revisitable through My Plus Ones in the same browser session. Clearing
the session or resetting an identity still removes that identity's access.
Explicitly resetting an identity cancels its upcoming meetups so the other
participant receives accurate guidance; it does not rewrite completed history.
Ended meeting windows move out of active handoffs into dashboard history.
Expired activity windows cannot create new matches or receive new agreement.

Waiting and live chat require JavaScript. With scripting disabled, an existing
confirmed plan displays this limitation and retains server-rendered form actions;
participants must refresh to see the latest state.

## Persistence and recovery

Migration `0015_versioned_meetup_plan` snapshots the original place and times
for existing matches, retaining null historical end times and old agreement
flags. It does not invent confirmation timestamps, arrivals, feedback, messages,
or analytics events. New matches without an original expected end use a
one-hour window.

Meetup writes use the same user/card/match lock order as existing chat writes.
UUID request records make successful retries durable. Safety moderation runs
outside those locks, followed by a second revision and availability check.
Stale confirmation is refused rather than applied to an unseen new plan.
Uncertain browser requests keep their original UUID and payload across reload
until a retry establishes their result. Existing message recovery is retained.

## Validation and deployment

Domain and endpoint tests cover plan revisions, confirmations, replay/conflict,
moderation, participant access, action windows, cancellation, recruitment,
feedback, identity retirement, retention, and historical migration. A
PostgreSQL test covers competing plan edit and confirmation. Browser tests
exercise two independent sessions, mobile waiting/handoff views, response loss
and reload recovery, stale consent, arrival, delay, cancellation, and reopening.
Browser activity fixtures live only in the isolated test database.

Verified on 2026-10-03: all 256 Django tests passed on PostgreSQL 16; the
SQLite suite passed with five PostgreSQL concurrency tests skipped. All twelve
JavaScript tests and all six Chromium browser flows passed. Schema drift and
whitespace checks were clean. Browser safety checks used explicit development
rules mode; this does not verify a live external moderation provider or Render
deployment.

| Acceptance scenario | Verified behavior |
| --- | --- |
| Wait, leave, and return | The same waiting deadline remains; both recent page signals start one fixed five-minute chat. |
| Shared plan and conflicting edits | New point/time reaches both pages; unsaved edits require an explicit choice; changed revisions clear consent without extending chat. |
| Lost responses and reload | Original message/plan/feedback requests can be reconciled without duplicate writes or lost input. |
| Agreement and follow-through | Both confirm the same revision; the composer closes; the dashboard reopens the accepted plan; arrival and delay do not claim a verified meetup. |
| Personal feedback | Met/not-met reports remain independent; arrival does not automatically record attendance; feedback survives a lost response and reload. |
| Cancellation, report, and identity reset | Travel guidance changes for both people; the card stays paused until its publisher explicitly reopens valid recruitment. |
| JavaScript disabled | The limitation is visible and an existing handoff's native arrival form works. |
| History and timing boundaries | Ended plans leave active handoffs; expired windows refuse new interest/consent; legacy feedback and its retention window remain consistent. |

Acceptance testing also corrected stale edit-page cancellation and stale
exit-match messages after agreement. Cloud working data remains empty of
activities and matches; browser-created activities use the isolated test
database only.

Production rollout requires the normal database backup and migration step,
then the application/static release. An old application instance does not
understand paused cards or versioned plans, so finish switching instances
before allowing new plan mutations. Do not roll the schema back after users
have recorded new meetup state. This working-copy change does not deploy the
hosted Render service.

The next product check is a real two-person mobile session: can each person
understand the waiting timer, identify the agreed point/time, and find the plan
again without instructions? Automated tests do not establish that outcome.

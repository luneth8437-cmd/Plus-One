# Product metrics and operating checks

These commands prepare a reviewable release and a small real-user pilot. They do
not deploy Render, change a paid plan, enable matching, run a production cleanup,
or prove that the hosted service is running the current working tree. Use an
isolated database for test traffic; never create pretend public activities to
make Discover look active.

## Read the product funnel

```sh
python manage.py funnel_report --days 7 --json
```

There are two explicit time bases:

- `funnel`, `meetup_feedback`, and `opening_assistant` use the activity creation
  cohort and its subsequent retained events.
- `journey` uses event time for Discover visits, empty pages, creation starts,
  rendered cards, Interested results, and reasons a waiting room closed. A
  rendered card is not proof that a user scrolled to it. Own cards are excluded
  from `rendered_card_impressions`.

`matches_with_meetup_confirmed` and its original conversion remain compatibility
counters for **at least one person self-reporting met**. They are not bilateral
success or verified attendance. `meetup_feedback` instead separates both met,
both not met, conflicting reports, one-sided feedback, no feedback, and
cancellation. Its denominator contains agreements whose meeting window ended
more than 24 hours ago, after the feedback opportunity. Future meetings and
unknown historical timing remain separate. No old feedback or timing is
invented. New events retain only actor role and timing evidence so business-row
cleanup does not erase this classification.

AI opener statistics distinguish raw clicks from unique known suggestion
batches selected at least once. Repeated selection cannot push the latter rate
above 100%. Old suggestions without batch identity remain unattributed. Version,
model, and actual generation/fallback strategy are grouped when known; missing
metadata is explicitly unknown. Message events contain adoption classification
and batch metadata, never user-written chat text. A short common greeting is
not automatically counted as an edited AI suggestion.

## Inspect a candidate runtime

```sh
python manage.py production_audit
python manage.py production_audit --expected-commit FULL_40_CHARACTER_SHA --strict
```

The output contains credential-presence booleans, database liveness, pending
migrations, matching/moderation/DEBUG settings, retained successful maintenance
history, and count-only maintenance backlog. It does not expose credentials,
connection strings, report descriptions, or chat content. A local checkout SHA
is identified as local; `RENDER_GIT_COMMIT`, when present and valid, is identified
as the platform-provided SHA.

`--strict` fails on configuration problems, including disabled new matching.
Matching should remain deliberately disabled while a release is being migrated;
read the facts without `--strict` at that stage. The command never enables it.
It does not call an external model or make a paid request, so present credentials
do not establish that moderation works. Actual hosted plans/database expiry,
backup restoration, external-service behavior, scheduling/alert delivery, and a
real two-person mobile experience remain explicitly unverified by this command.

## Review and apply maintenance

```sh
python manage.py maintain_product --batch-size 20
python manage.py maintain_product --batch-size 20 --commit
```

The first command is a nonmutating preview. It reports pending expiry and
current cleanup eligibility; applying expiry can make additional identities
eligible. It does not fabricate a successful maintenance record.

The explicit commit command advances expiry before bounded cleanup. A deferred
expiry stops cleanup and fails the command. Successful completion records only
timestamps and counts, separate from product funnel events. Each selected
identity also cascades its ownership graph, so `--batch-size` does not cap every
deleted child row. Protected live matches, recent agreements, and recent
unresolved reports remain protected. Activity reports protect their publisher
and reporter using the original creation clock. Expired browser-budget buckets
and terminal-match presence leases are cleaned in bounded batches; unknown
lease expiry is not fabricated. Expired leases of live matches retain sequence
tombstones so delayed old heartbeats cannot restore false presence. Inactive
push subscriptions of retired identities, or subscriptions inactive for over
30 days, can be removed; active subscriptions remain. Delivery ledgers older
than 90 days can be removed once their notice is no longer eligible for push.
A notice still eligible for delivery keeps its deduplication key until that
eligibility ends.

Before scheduling production maintenance, review a production preview, verify a
backup restoration in a separate database, and assess the ownership-graph size.
The repository does not enable a paid Cron service or automatically run this
command. An operator can configure their existing authorized scheduler to run
the commit command and alert on a nonzero exit. Inspect the last successful
record, its age, remaining expired live rows, and maintenance backlog afterward.
No retained record means unknown, not proof that no external job exists.

## Configure background reminders

In-site polling only works while the product is open. A background WebPush
reminder requires all of: HTTPS, a supported browser, a real subscription
explicitly enabled by its user, a configured VAPID identity, and a running
delivery worker. Permission alone does not deliver notifications.

Review the `send_push_updates` command's dry-run output first:

```sh
python manage.py send_push_updates --batch-size 20
python manage.py send_push_updates --batch-size 20 --send
```

The second command sends eligible updates to opted-in users and contacts their
push providers. It is an explicit operational action, not a local health check.
Configure `PLUSONE_WEB_PUSH_ENABLED` and the matching
`PLUSONE_VAPID_PUBLIC_KEY`, `PLUSONE_VAPID_PRIVATE_KEY`, and
`PLUSONE_VAPID_SUBJECT` in the actual runtime without committing private keys.
Configure an existing authorized scheduler to run its explicit send mode about once a
minute only after the production VAPID configuration and genuine-device smoke
test succeed. The repository does not start a paid worker/Cron resource or
register anybody for notifications automatically. Do not send test reminders
to real users. Verify subscription disabling on identity reset, retry/error
handling, cancellation messages, and that the delivery ledger prevents the
same notice from being resent. Operating-system notification suppression and
browser delivery delays remain outside the application's timing guarantee.
See [Web Push delivery](web_push.md) for provider validation, dispatch leases,
retry limits, and device acceptance details.

## Configure actual public campus locations

Prepare a JSON array using the real campus's exact public-place names. Every
item requires `name`, `area`, and `location_type` (`dining`, `sports`, `study`,
`event`, `outdoor`, or `other`). Optional `latitude` and `longitude` must be
supplied together as valid degrees with at most six decimals. Do not substitute
invented addresses, private rooms, or private contact information.

```sh
python manage.py configure_campus_locations --file actual-campus-locations.json
python manage.py configure_campus_locations --file actual-campus-locations.json --commit
```

The default only previews. Imports match exact names, are idempotent, and keep
existing coordinates when omitted. Unlisted places are preserved by default.
To review unused unlisted places for removal, add
`--remove-unused-unlisted` to the preview; apply the same option with `--commit`
only after reviewing that list. A place referenced by any historical or current
activity is protected and never deleted by the importer. The tool cannot infer
which place is real or merely generic from its name.

## Real two-person pilot record

Recruit a small voluntary group for one actual campus, one frequent activity,
and a common use window. Published activities must represent people who really
intend to participate. Keep pilot/test cohorts separate from general traffic;
the July scripted funnel is instrumentation evidence only.

For each pair, record the released SHA, actual device/browser, task result,
relative duration, and anonymized observations. Do not commit names, contact
details, or private chats. Have each person use their own phone and browser
without facilitator instruction:

1. Understand the empty or populated Discover screen and publish a genuine
   card. Distinguish missing supply from trouble creating or finding the card.
2. Express interest while the publisher is on the site, then repeat a controlled
   attempt with the publisher backgrounded or screen locked. Check the actual
   reminder limitations; don't promise a notification outside supported modes.
3. Enter the waiting room, return after leaving it, and explain when the
   five-minute chat starts without being told.
4. Change the point/time, handle concurrent edits, and have both independently
   state the final accepted plan. Neither person should act on stale consent.
5. Find the plan again, report delay/arrival, and test a cancellation before
   travel. Check that each person learns about the changed plan.
6. After the real meeting window, collect independent met/not-met feedback.
   Keep disagreement and missing responses visible; arrival is not attendance.
7. Ask whether they would use it again for another real need. Measure any
   spontaneous return separately from facilitated tasks. A temporary browser
   identity is a proxy for a returning browser, not a verified returning person.

Write success criteria before recruiting: no-help completion, correct final
point/time for both people, time to find the plan again, waiting-to-chat rate and
waiting time, cancellation awareness, and matured bilateral feedback. Report
sample size and unobserved outcomes. Small pilot counts cannot establish a
general conversion rate or product-market fit.

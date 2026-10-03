# Opt-in background reminders

Web Push delivers a short, private reminder through the browser's push service,
including when the Plus One page is closed. This complements the in-site inbox;
permission alone does not configure server delivery. This change does not deploy
a worker, create real VAPID keys, or send messages to real subscriptions.
The broader deployment, maintenance and real-device pilot checklist is in
[Product operations](product_operations.md).

## Deployment configuration

The HTTPS web service and a hosted scheduled worker need the same application
database and configuration. Install `requirements.lock`, apply migrations, and
set these environment variables through the hosting platform's secret settings:

| Variable | Purpose |
| --- | --- |
| `PLUSONE_WEB_PUSH_ENABLED` | Explicitly set `True` after configuring and checking the delivery worker. Default `False`. |
| `PLUSONE_VAPID_PUBLIC_KEY` | Base64url uncompressed P-256 public point, from the operator's VAPID key pair. Public by design. |
| `PLUSONE_VAPID_PRIVATE_KEY` | Matching base64url 32-byte private scalar. Keep only in platform secrets; never commit or expose it to the browser. |
| `PLUSONE_VAPID_SUBJECT` | Real operator contact: `mailto:address@domain` or an HTTPS contact page. |
| `PLUSONE_WEB_PUSH_ALLOWED_HOSTS` | Optional comma-separated subset of the supported browser-provider hosts. Arbitrary custom hosts are rejected. |

Supported provider hosts are `fcm.googleapis.com`,
`updates.push.services.mozilla.com`, `push.services.mozilla.com`,
`*.notify.windows.com`, and `web.push.apple.com`. HTTPS, normal port 443, no URL
credentials, valid P-256/auth material, and an allowed host are required. Outbound
requests retain normal TLS verification, do not follow redirects, and do not use
environment proxies. An operator requiring additional browser-provider hosts
must review and change the explicit provider list.

The web configuration endpoint exposes only readiness and the public key. Missing,
malformed, or mismatched configuration leaves reminders unavailable. Serve the
root-scoped `/service-worker.js` on HTTPS with the app's existing session and CSRF
protections. Browser opt-in is initiated by the user's reminder control. Safari
on iOS/iPadOS may require an installed home-screen web app; support and delivery
also depend on browser and operating-system permission settings.

## Hosted worker

First inspect a preview:

```bash
python manage.py send_push_updates --batch-size 100
```

Previewing never sends, claims delivery records, or changes lifecycle state. It
reports counts and readiness without printing endpoints, browser encryption keys,
operator keys, or provider response bodies.

Once the operator has authorized delivery, schedule this command on Render or
another server using the same database and secret settings:

```bash
python manage.py send_push_updates --send --batch-size 100
```

Run about once per minute so a ten-minute waiting window can receive a timely
reminder. `--send` is explicit because it contacts external browser push providers
and records delivery attempts. A run scans and attempts a bounded batch; larger
installations need a suitable cadence and capacity. PostgreSQL is required for
concurrent-worker locking guarantees. No assistant-owned server is involved.

Worker health and successful provider acceptance must be checked on the hosting
platform before advertising working background delivery. Provider acceptance
does not prove that the operating system displayed or that a person read a
notification. Test one operator-owned browser/device with the page closed, then
confirm the notification opens its participant-only plan. Repeat for the target
mobile browsers. Live device acceptance was not performed by the mocked tests.

## Delivery and privacy rules

- Only the subscribed actor's current participant updates are eligible. Old
  waiting rooms, expired chat clocks, cancelled plans, retired identities and
  blocked relationships cannot emit a ready-plan reminder. A cancellation alert
  remains useful and may still be delivered.
- Normal updates older than ten minutes are skipped. The meeting reminder is
  eligible from 30 minutes before the accepted meeting time until 15 minutes
  after it, provided the plan is still active and its end has not passed.
- Push bodies are generic. They omit activity titles, conversation messages,
  meeting points, participant identities, endpoints, and private keys. Links open
  the app, which checks current-session access again.
- Each subscription stores a durable delivery record per stable update key.
  Claims hold a short lease; network calls occur after database locks are released.
  Retry delays increase, attempts stop at five, and HTTP 404/410 disables the
  subscription. Provider errors are stored as codes only.
- Stable notification tags allow the service worker to replace repeated display.
  A crash after provider acceptance but before recording success can cause a
  retry; exactly-once receipt is not promised.
- Users can disable their own subscription. Identity reset deactivates old-user
  subscriptions. An inactive endpoint can transfer to the new identity only if
  its former owner is retired and both browser encryption keys exactly match;
  old delivery history is removed during that transfer. Active foreign
  subscriptions cannot be taken over. Stored records cascade when their owning
  identity is deleted by normal ephemeral-data cleanup. Maintenance also removes
  inactive subscriptions after their retention window; inactive subscriptions of
  retired identities can be removed sooner. Deactivation updates the retention
  clock so a newly disabled device is not mistaken for an old inactive record.

`plusone.tests_push_notifications` exercises provider validation, permission
configuration, ownership, reset transfer, private payloads, participant access,
freshness, blocked-state convergence, bounded retries, delivery leases, redirects,
fair worker scans and the command's safe default using fixed synthetic test
vectors and mocked providers. It makes no real push-service requests.

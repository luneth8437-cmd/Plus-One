const test = require("node:test");
const assert = require("node:assert/strict");
const { decodeVapidPublicKey, sameOriginPushUrl, readAlertPreference,
  enablePushDelivery, deactivatePushDelivery } = require("../../plusone/static/plusone/app.js");
const { notificationUrl, notificationOptions, openNotification } = require("../../plusone/static/plusone/service-worker.js");

const origin = "https://plusone.example";
const keyBytes = Uint8Array.from({ length: 65 }, (_, index) => index === 0 ? 4 : index);
const publicKey = Buffer.from(keyBytes).toString("base64url");

function browserFixture({ existing = false, oldKey = false, unsubscribeFails = false } = {}) {
  const operations = [];
  const subscription = { endpoint: "https://push.example/device-1",
    options: { applicationServerKey: oldKey ? Uint8Array.from(keyBytes, (value, index) => index === 2 ? 99 : value).buffer : keyBytes.buffer },
    toJSON: () => ({ endpoint: "https://push.example/device-1", keys: { auth: "auth", p256dh: "key" } }),
    unsubscribe: async () => { operations.push("browser-unsubscribe"); if (unsubscribeFails) throw new Error("browser unavailable"); return true; },
  };
  const registration = { active: { state: "activated" }, pushManager: {
    getSubscription: async () => existing ? subscription : null,
    subscribe: async (options) => { operations.push("browser-subscribe"); assert.equal(options.userVisibleOnly, true); assert.deepEqual(options.applicationServerKey, keyBytes); return subscription; },
  } };
  const serviceWorker = {
    register: async (url, options) => { operations.push("register"); assert.equal(url, "/service-worker.js"); assert.deepEqual(options, { scope: "/" }); return registration; },
    getRegistration: async () => registration,
  };
  return { operations, subscription, serviceWorker };
}

test("a public VAPID key decodes into the exact uncompressed browser key", () => {
  assert.deepEqual(decodeVapidPublicKey(publicKey), keyBytes);
  assert.throws(() => decodeVapidPublicKey("not a key"));
  assert.throws(() => decodeVapidPublicKey(Buffer.alloc(65).toString("base64url")));
  assert.throws(() => decodeVapidPublicKey("AQID"));
});

test("push subscription settings cannot redirect a CSRF-bearing request off-site", () => {
  assert.equal(sameOriginPushUrl("/notifications/push/subscribe/", origin), `${origin}/notifications/push/subscribe/`);
  for (const url of [undefined, "", "https://evil.example/steal", "//evil.example/steal", "javascript:alert(1)"]) {
    assert.equal(sameOriginPushUrl(url, origin), null);
  }
});

test("browser subscriptions become background-enabled only after the server accepts them", async () => {
  const fixture = browserFixture();
  const post = async (url, payload) => { fixture.operations.push("server-subscribe"); assert.equal(url, "/subscribe/"); assert.deepEqual(payload, { subscription: fixture.subscription.toJSON() }); };
  const subscription = await enablePushDelivery({ serviceWorker: fixture.serviceWorker, publicKey, subscribeUrl: "/subscribe/", post });
  assert.equal(subscription, fixture.subscription);
  assert.deepEqual(fixture.operations, ["register", "browser-subscribe", "server-subscribe"]);
  let candidate = null;
  await assert.rejects(enablePushDelivery({ serviceWorker: fixture.serviceWorker, publicKey, subscribeUrl: "/subscribe/",
    onSubscription: (subscription) => { candidate = subscription.endpoint; },
    post: async () => { throw new Error("identity changed"); } }), /identity changed/);
  assert.equal(candidate, fixture.subscription.endpoint, "an unconfirmed server write remains possible to reconcile or deactivate");
});

test("a current subscription is reused without a second permission prompt or duplicate browser subscription", async () => {
  const fixture = browserFixture({ existing: true });
  await enablePushDelivery({ serviceWorker: fixture.serviceWorker, publicKey, subscribeUrl: "/subscribe/", post: async () => fixture.operations.push("server-subscribe") });
  assert.deepEqual(fixture.operations, ["register", "server-subscribe"]);
});

test("key rotation closes server delivery before replacing the browser subscription", async () => {
  const fixture = browserFixture({ existing: true, oldKey: true });
  await enablePushDelivery({ serviceWorker: fixture.serviceWorker, publicKey, subscribeUrl: "/subscribe/",
    beforeReplace: async () => fixture.operations.push("server-unsubscribe"), post: async () => fixture.operations.push("server-subscribe") });
  assert.deepEqual(fixture.operations, ["register", "server-unsubscribe", "browser-unsubscribe", "browser-subscribe", "server-subscribe"]);
});

test("turning alerts off never abandons browser credentials before the server deactivates delivery", async () => {
  const fixture = browserFixture({ existing: true });
  const result = await deactivatePushDelivery({ serviceWorker: fixture.serviceWorker, endpoint: fixture.subscription.endpoint,
    unsubscribeUrl: "/unsubscribe/", post: async (_url, body) => { assert.equal(body.endpoint, fixture.subscription.endpoint); fixture.operations.push("server-unsubscribe"); } });
  assert.deepEqual(fixture.operations, ["server-unsubscribe", "browser-unsubscribe"]);
  assert.deepEqual(result, { server_deactivated: true, browser_unsubscribed: true });
  fixture.operations.length = 0;
  await assert.rejects(deactivatePushDelivery({ serviceWorker: fixture.serviceWorker, endpoint: fixture.subscription.endpoint,
    unsubscribeUrl: "/unsubscribe/", post: async () => { throw new Error("network unavailable"); } }), /network unavailable/);
  assert.deepEqual(fixture.operations, []);
});

test("revoked browser permission still permits server cleanup using the saved endpoint", async () => {
  const sent = [];
  const result = await deactivatePushDelivery({ serviceWorker: { getRegistration: async () => { throw new Error("permission revoked"); } },
    endpoint: "https://push.example/old-device", unsubscribeUrl: "/unsubscribe/", post: async (_url, body) => sent.push(body.endpoint) });
  assert.deepEqual(sent, ["https://push.example/old-device"]);
  assert.equal(result.server_deactivated, true);
  const fixture = browserFixture({ existing: true, unsubscribeFails: true });
  assert.deepEqual(await deactivatePushDelivery({ serviceWorker: fixture.serviceWorker, endpoint: fixture.subscription.endpoint,
    unsubscribeUrl: "/unsubscribe/", post: async () => {} }), { server_deactivated: true, browser_unsubscribed: false });
});

test("old foreground opt-in survives an upgrade while malformed preferences never enable delivery", () => {
  assert.deepEqual(readAlertPreference("enabled"), { enabled: true, push_endpoint: "", push_confirmed: false });
  assert.deepEqual(readAlertPreference('{"enabled":true,"push_endpoint":"https://push.example/device"}'), { enabled: true, push_endpoint: "https://push.example/device", push_confirmed: false });
  assert.equal(readAlertPreference('{"enabled":true,"push_endpoint":"https://push.example/device","push_confirmed":true}').push_confirmed, true);
  assert.equal(readAlertPreference('{"enabled":"yes"}').enabled, false);
  assert.equal(readAlertPreference("broken").enabled, false);
});

test("a pushed link is restricted to a same-origin plan or updates page", () => {
  assert.equal(notificationUrl("/chat/42/", origin), `${origin}/chat/42/`);
  for (const url of ["https://evil.example/chat/42/", "//evil.example/", "javascript:alert(1)", "/session/", "%%%", null]) {
    assert.equal(notificationUrl(url, origin), `${origin}/dashboard/`);
  }
  const notification = notificationOptions({ title: "Plan changed", body: "Review the new time", tag: "plusone:event:42", url: "https://evil.example/" }, origin);
  assert.equal(notification.options.tag, "plusone:event:42");
  assert.equal(notification.options.data.url, `${origin}/dashboard/`);
});

test("opening a pushed plan focuses its existing tab and preserves drafts in other tabs", async () => {
  const operations = [];
  const clients = { matchAll: async () => [{ url: `${origin}/chat/42/`, focus: async () => operations.push("focus-plan") },
    { url: `${origin}/create/`, focus: async () => operations.push("focus-draft") }], openWindow: async (url) => operations.push(url) };
  await openNotification(clients, "/chat/42/", origin);
  await openNotification(clients, "/chat/43/", origin);
  assert.deepEqual(operations, ["focus-plan", `${origin}/chat/43/`]);
});

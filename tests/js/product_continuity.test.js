const test = require("node:test");
const assert = require("node:assert/strict");
const {
  sessionScopedKey,
  reviewedPayloadEntries,
  proposalEntries,
  restoreFormPayload,
  previewStartTimeLabel,
  createPresenceTracker,
  planActionAllowed,
  waitingReasonMessage,
  openerClickEntries,
  dialogTabTarget,
} = require("../../plusone/static/plusone/app.js");

test("identity reset cannot read a previous identity's drafts or uncertain writes", () => {
  for (const kind of ["create-draft", "create-request", "chat-draft:17", "plan-edits:17", "plan-request:17"]) {
    const previous = sessionScopedKey(kind, "old-guest");
    const current = sessionScopedKey(kind, "new-guest");
    const saved = new Map([[previous, { requestId: "potentially-already-published", text: "Private draft" }]]);
    assert.notEqual(previous, current);
    assert.equal(saved.get(current), undefined);
  }
});

test("an AI review snapshot retains manual fields without transporting identity or replay tokens", () => {
  const entries = reviewedPayloadEntries([
    ["title", "My edited title"], ["description", "Meet at the north entrance"],
    ["location", "12"], ["start_time", "2026-10-03T18:30"],
    ["csrfmiddlewaretoken", "current-token"], ["request_id", "publish-id"],
    ["session_scope", "identity-token"], ["action", "publish"], ["confirm_date", "yes"],
  ]);
  assert.deepEqual(entries, [
    ["title", "My edited title"], ["description", "Meet at the north entrance"],
    ["location", "12"], ["start_time", "2026-10-03T18:30"],
  ]);
});

test("suggested unknown values stay empty and cannot replace request ownership fields", () => {
  const entries = proposalEntries({ title: "Suggested", location: null, expected_end_time: null,
    expire_minutes: 45, request_id: "foreign-request", session_scope: "foreign-user", csrfmiddlewaretoken: "foreign-token" });
  assert.deepEqual(entries, [["title", "Suggested"], ["location", ""], ["expected_end_time", ""], ["expire_minutes", "45"]]);
  const form = { elements: [
    { name: "title", type: "text", value: "Reviewed" },
    { name: "request_id", type: "hidden", value: "current-request" },
    { name: "session_scope", type: "hidden", value: "current-identity" },
    { name: "csrfmiddlewaretoken", type: "hidden", value: "current-token" },
  ] };
  restoreFormPayload(form, JSON.stringify([["title", "Restored"], ["request_id", "other"], ["session_scope", "other"], ["csrfmiddlewaretoken", "other"]]));
  assert.deepEqual(form.elements.map((field) => field.value), ["Restored", "current-request", "current-identity", "current-token"]);
});

test("malformed saved data is rejected before it can partially change a form", () => {
  const form = { elements: [{ name: "title", type: "text", value: "Kept" }] };
  assert.equal(restoreFormPayload(form, JSON.stringify([["title", "Changed"], "broken"])), false);
  assert.equal(form.elements[0].value, "Kept");
});

test("a hidden tab's presence update cannot share another tab's sequence", () => {
  const first = createPresenceTracker("first-document");
  const second = createPresenceTracker("second-document");
  assert.deepEqual(first.next(true), { tab_id: "first-document", sequence: 1, visible: true });
  assert.deepEqual(second.next(true), { tab_id: "second-document", sequence: 1, visible: true });
  assert.deepEqual(first.next(false), { tab_id: "first-document", sequence: 2, visible: false });
  assert.deepEqual(second.next(true), { tab_id: "second-document", sequence: 2, visible: true });
  assert.deepEqual(first.next(true), { tab_id: "first-document", sequence: 3, visible: true });
  assert.notEqual(createPresenceTracker().next(true).tab_id, createPresenceTracker().next(true).tab_id);
});

test("campus wall-time preview does not shift a device's daylight-saving gap", () => {
  const previous = process.env.TZ;
  try {
    process.env.TZ = "America/New_York";
    assert.match(previewStartTimeLabel("2026-03-08T02:30"), /8, 02:30$/);
    process.env.TZ = "Pacific/Honolulu";
    assert.match(previewStartTimeLabel("2026-10-03T00:05"), /3, 00:05$/);
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

test("opening positive feedback does not prematurely open irreversible no-meetup feedback", () => {
  const plan = { can_outcome: true, can_met: true, can_not_met: false };
  assert.equal(planActionAllowed(plan, "outcome", "met"), true);
  assert.equal(planActionAllowed(plan, "outcome", "not_met"), false);
  assert.equal(planActionAllowed({ ...plan, can_not_met: true }, "outcome", "not_met"), true);
  assert.equal(planActionAllowed({ ...plan, can_met: false }, "outcome", "met"), false);
});

test("waiting explanations distinguish a busy viewer without exposing someone else's activity", () => {
  assert.match(waitingReasonMessage("viewer_busy"), /You already have a live chat/);
  assert.match(waitingReasonMessage("other_busy"), /Your Plus One is finishing another chat/);
  assert.match(waitingReasonMessage("both_busy"), /You are both in another live chat/);
  assert.equal(waitingReasonMessage("waiting_for_presence"), "");
  assert.equal(waitingReasonMessage("unknown"), "");
});

test("a stale suggested message remains attributed to its displayed batch rather than a newer batch", () => {
  const old = new Map(openerClickEntries({ openerIndex: "0", batchId: "old-batch" }));
  const current = new Map(openerClickEntries({ openerIndex: "0", batchId: "new-batch" }));
  assert.equal(old.get("batch_id"), "old-batch");
  assert.equal(current.get("batch_id"), "new-batch");
  assert.equal(old.get("index"), "0");
  assert.equal(openerClickEntries({ openerIndex: "0", batchId: "" }), null);
  assert.equal(openerClickEntries({ openerIndex: "-1", batchId: "batch" }), null);
});

test("dialog keyboard traversal wraps both directions and recovers an outside active element", () => {
  const openChat = {}, keepSwiping = {}, close = {};
  const controls = [openChat, keepSwiping, close];
  assert.equal(dialogTabTarget(controls, openChat), keepSwiping);
  assert.equal(dialogTabTarget(controls, close), openChat);
  assert.equal(dialogTabTarget(controls, openChat, true), close);
  assert.equal(dialogTabTarget(controls, {}), openChat);
  assert.equal(dialogTabTarget(controls, {}, true), close);
  assert.equal(dialogTabTarget([], {}), null);
});

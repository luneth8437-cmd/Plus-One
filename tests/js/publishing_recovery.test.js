const test = require("node:test");
const assert = require("node:assert/strict");
const {
  createEntriesFingerprint,
  restoreNewerCreateDraft,
  clearCreateFields,
  publishedRequestLeavesDraft,
} = require("../../plusone/static/plusone/app.js");

test("review refresh restores later edits while preserving server corrections at the same revision", () => {
  const saved = { entries: [["title", "Later edit B"]], revision: 6 };
  assert.equal(restoreNewerCreateDraft(saved, true, "review-token", 5), true);
  assert.equal(restoreNewerCreateDraft(saved, true, "review-token", 6), false);
  assert.equal(restoreNewerCreateDraft(saved, false, "review-token", 5), true);
  assert.equal(restoreNewerCreateDraft({ entries: [["title", "Legacy draft"]] }, true, "", 0), true);
  assert.equal(restoreNewerCreateDraft({ entries: [["title", "Legacy draft"]] }, false, "", 0), false);
  assert.equal(restoreNewerCreateDraft(null, true, "", 0), false);
});

test("accepting original A clears only its own draft, retaining later B and later assist edits", () => {
  const entries = [["title", "Original A"], ["location", "12"]];
  const request = { fingerprint: createEntriesFingerprint(entries), draftRevision: 4, assistText: "Study tomorrow" };
  const original = { entries, revision: 4, assistText: "Study tomorrow" };
  assert.equal(publishedRequestLeavesDraft(original, request), false);
  assert.equal(publishedRequestLeavesDraft({ ...original, entries: [["title", "Later B"], ["location", "12"]] }, request), true);
  assert.equal(publishedRequestLeavesDraft({ ...original, revision: 5 }, request), true);
  assert.equal(publishedRequestLeavesDraft({ ...original, assistText: "A later sentence" }, request), true);
  assert.equal(publishedRequestLeavesDraft(null, request), false);
});

test("discard blanks server-bound fields instead of restoring their initial values", () => {
  const fields = ["title", "description", "activity_type", "location", "start_time", "expected_end_time", "raw_text", "assist_text"]
    .map((name) => ({ name, value: "Old server-bound value", defaultValue: "Old server-bound value" }));
  const form = { elements: [
    ...fields,
    { name: "expire_minutes", value: "120" },
    { name: "confirm_date", checked: true },
    { name: "reviewed_payload", value: '{"title":"Original A"}' },
    { name: "proposal_payload", value: '{"title":"Suggestion C"}' },
    { name: "review_token", value: "review-to-discard" },
    { name: "session_scope", value: "current-user" },
    { name: "csrfmiddlewaretoken", value: "current-csrf" },
  ] };
  clearCreateFields(form);
  assert.ok(fields.every((field) => field.value === ""));
  const byName = Object.fromEntries(form.elements.map((field) => [field.name, field]));
  assert.equal(byName.expire_minutes.value, "45");
  assert.equal(byName.confirm_date.checked, false);
  assert.equal(byName.reviewed_payload.value, "{}");
  assert.equal(byName.proposal_payload.value, "{}");
  assert.equal(byName.review_token.value, "review-to-discard");
  assert.equal(byName.session_scope.value, "current-user");
  assert.equal(byName.csrfmiddlewaretoken.value, "current-csrf");
});

test("request comparison ignores AI sentences, ownership, and review metadata", () => {
  const original = [["title", "Reviewed card"], ["location", "12"]];
  const auxiliary = [["assist_text", "A different draft"], ["proposal_payload", "{}"], ["reviewed_payload", "{}"],
    ["review_token", "new-review"], ["draft_revision", "19"], ["request_id", "new-id"], ["session_scope", "same-user"]];
  assert.equal(createEntriesFingerprint(original), createEntriesFingerprint([...auxiliary, ...original.reverse()]));
});

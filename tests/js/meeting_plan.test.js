const test = require("node:test");
const assert = require("node:assert/strict");
const { createPlanRequest, planActionAllowed, meetupParticipantLabel, meetupPlanPresentation } = require("../../plusone/static/plusone/app.js");

test("a lost plan response can be retried with its original revision and fields after later edits", () => {
  const formValues = [
    ["action", "update_plan"], ["revision", "3"], ["meeting_point", "Library entrance"],
    ["meeting_at", "2026-10-03T18:00"], ["expected_end_at", "2026-10-03T19:00"],
    ["csrfmiddlewaretoken", "browser-only-csrf"], ["request_id", "discard-form-id"],
  ];
  const pending = createPlanRequest(formValues, "same-retry-id");
  formValues[2][1] = "A different entrance";
  const restored = JSON.parse(JSON.stringify(pending));
  const body = new Map(restored.entries);
  assert.equal(restored.requestId, "same-retry-id");
  assert.equal(body.get("revision"), "3");
  assert.equal(body.get("meeting_point"), "Library entrance");
  assert.equal(body.get("meeting_at"), "2026-10-03T18:00");
  assert.equal(body.has("csrfmiddlewaretoken"), false);
  assert.equal(body.has("request_id"), false);
});

test("confirmed history does not enable new meetup actions without current server permission", () => {
  const cancelled = { meetup_status: "cancelled", can_arrive: false, can_delay: false, can_cancel: false, can_outcome: true, can_reopen_card: false };
  assert.equal(planActionAllowed(cancelled, "arrived"), false);
  assert.equal(planActionAllowed(cancelled, "delayed"), false);
  assert.equal(planActionAllowed(cancelled, "cancel_meetup"), false);
  assert.equal(planActionAllowed(cancelled, "outcome"), true);
  assert.equal(planActionAllowed(cancelled, "reopen_card"), false);
  assert.equal(planActionAllowed(null, "confirm_plan"), false);
});

test("arrival and delay labels never claim that an actual meetup was verified", () => {
  assert.equal(meetupParticipantLabel("arrived", 0, ""), "Marked arrived");
  assert.equal(meetupParticipantLabel("delayed", 10, ""), "Delay reported; arrival estimate not recorded.");
  assert.equal(meetupParticipantLabel("pending", 0, ""), "Not marked arrived");
  assert.equal(meetupParticipantLabel("arrived", 0, "met"), "Reported: we met");
  assert.equal(meetupParticipantLabel("pending", 0, "not_met"), "Reported: we didn't meet");
});

test("an ended window stays historical regardless of arrival or self-reported attendance", () => {
  for (const viewerOutcome of ["", "met", "not_met"]) {
    const presentation = meetupPlanPresentation({ meetup_status: "finished", viewer_status: "arrived", viewer_outcome: viewerOutcome });
    assert.equal(presentation.phaseLabel, "Meeting window ended");
    assert.doesNotMatch(presentation.title, /ready/i);
    assert.match(presentation.guidance, /not proof that you met/);
  }
  const cancelled = meetupPlanPresentation({ meetup_status: "cancelled", viewer_outcome: "met" });
  assert.match(cancelled.guidance, /Nobody should travel/);
});

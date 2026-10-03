const test = require("node:test");
const assert = require("node:assert/strict");
const { meetupParticipantLabel, planActionAllowed, createPlanRequest } = require("../../plusone/static/plusone/app.js");

test("arrival estimates use the current server label and cannot invent a future time for old delays", () => {
  assert.equal(meetupParticipantLabel("delayed", 5, "", { arrival_label: "Expected around Oct 03, 20:05" }), "Expected around Oct 03, 20:05");
  assert.equal(meetupParticipantLabel("delayed", 5, "", { arrival_label: "Arrival estimate has passed. Send a new update." }), "Arrival estimate has passed. Send a new update.");
  assert.match(meetupParticipantLabel("delayed", 10, ""), /not recorded/);
  assert.doesNotMatch(meetupParticipantLabel("delayed", 10, ""), /10 min|Expected/);
});

test("fixed coordination remains distinct from meeting feedback", () => {
  assert.equal(meetupParticipantLabel("arrived", 0, "", { arrival_label: "At the agreed point but cannot find you" }), "At the agreed point but cannot find you");
  assert.equal(meetupParticipantLabel("arrived", 0, "met", { arrival_label: "At the entrance of the agreed place" }), "Reported: we met");
  assert.equal(planActionAllowed({ can_coordinate: true, can_edit: false }, "coordination"), true);
  assert.equal(planActionAllowed({ can_coordinate: false, can_arrive: true }, "coordination"), false);
});

test("an accepted estimate expires locally even when the next server poll fails", () => {
  const details = { arrival_label: "Expected around Oct 03, 20:05", arrival_eta: "2026-10-03T12:05:00Z", historical: false };
  assert.match(meetupParticipantLabel("delayed", 5, "", details, Date.parse("2026-10-03T12:04:59Z")), /Expected around/);
  assert.match(meetupParticipantLabel("delayed", 5, "", details, Date.parse("2026-10-03T12:05:00Z")), /estimate has passed/);
  assert.match(meetupParticipantLabel("delayed", 5, "", { ...details, is_viewer: false }, Date.parse("2026-10-03T12:05:00Z")), /Awaiting/);
  assert.match(meetupParticipantLabel("delayed", 5, "", { ...details, is_viewer: true }, Date.parse("2026-10-03T12:05:00Z")), /Update your estimate/);
  assert.equal(meetupParticipantLabel("delayed", 5, "", { ...details, historical: true, arrival_label: "Last report: arrival estimate Oct 03, 20:05" }, Date.parse("2026-10-03T12:10:00Z")), "Last report: arrival estimate Oct 03, 20:05");
});

test("uncertain fixed-signal retry preserves the reviewed revision and original selection", () => {
  const values = [["action", "coordination"], ["revision", "2"], ["coordination_signal", "cant_find"]];
  const request = createPlanRequest(values, "same-fixed-signal-id");
  values[2][1] = "at_entrance";
  const restored = JSON.parse(JSON.stringify(request));
  assert.equal(new Map(restored.entries).get("coordination_signal"), "cant_find");
  assert.equal(new Map(restored.entries).get("revision"), "2");
});

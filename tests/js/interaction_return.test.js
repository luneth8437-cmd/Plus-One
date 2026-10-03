const test = require('node:test');
const assert = require('node:assert/strict');
const { changedPlanFields, syncPlanDateBounds, inboxAttentionItems } = require('../../plusone/static/plusone/app.js');

const basePlan = { revision: 2, meeting_point: 'North entrance', meeting_at_input: '2026-10-03T19:00', expected_end_at_input: '2026-10-03T20:00' };

test('an end-only revision is identifiable even when the start and meeting point stay the same', () => {
  assert.deepEqual(changedPlanFields(basePlan, { ...basePlan, revision: 3, expected_end_at_input: '2026-10-03T19:30' }), ['expected_end_at_input']);
  assert.deepEqual(changedPlanFields(basePlan, { ...basePlan }), []);
  assert.deepEqual(changedPlanFields(null, basePlan), []);
});

test('a return to the original meeting point still counts as a change from the previously viewed plan', () => {
  assert.deepEqual(changedPlanFields({ ...basePlan, meeting_point: 'Side lobby' }, basePlan), ['meeting_point']);
});

test('restored earlier draft times set the end minimum to the current draft, not the shared start', () => {
  const form = { elements: { meeting_at: { value: '2026-10-03T18:50' }, expected_end_at: { value: '2026-10-03T18:55' } } };
  syncPlanDateBounds(form, { ...basePlan, earliest_meeting_at_input: '2026-10-03T18:45', latest_end_at_input: '2026-10-03T20:00' });
  assert.equal(form.elements.expected_end_at.min, '2026-10-03T18:50');
  assert.equal(form.elements.meeting_at.min, '2026-10-03T18:45');
  assert.equal(form.elements.expected_end_at.max, '2026-10-03T20:00');
  assert.equal(form.elements.expected_end_at.value, '2026-10-03T18:55');
  form.elements.meeting_at.value = '2026-10-03T19:00';
  syncPlanDateBounds(form, { earliest_meeting_at_input: '2026-10-03T18:40', latest_end_at_input: '2026-10-03T19:30' });
  assert.equal(form.elements.expected_end_at.min, '2026-10-03T19:00');
  assert.equal(form.elements.meeting_at.max, '2026-10-03T19:30');
});

test('a read but unfinished feedback task survives while older updates for its plan merge into one entry', () => {
  const items = inboxAttentionItems([], [
    { id: 'arrival:8', match_id: 8, kind: 'update', message: 'Arrival updated', url: '/chat/8/', is_read: false, is_current: true },
    { id: 'feedback:8', match_id: 8, kind: 'task', message: 'Record meetup feedback', url: '/chat/8/', is_read: true, is_current: true, attention_required: true },
    { id: 'plan:8', match_id: 8, message: 'Meeting ready', url: '/chat/8/', is_read: false, is_current: false, attention_required: false },
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0].key, 'match:8');
  assert.equal(items[0].message, 'Record meetup feedback');
});

test('obsolete unread notices and non-actionable confirmed-match fallbacks cannot reclaim the attention banner', () => {
  assert.deepEqual(inboxAttentionItems([{ id: 9, status: 'agreed', title: 'Finished plan', url: '/chat/9/' }], [
    { id: 'old:9', match_id: 9, is_read: false, is_current: false, url: '/chat/9/' },
  ]), []);
});

test('a waiting match remains reachable when notification delivery has no current rows', () => {
  const items = inboxAttentionItems([{ id: 9, status: 'waiting', title: 'Study', url: '/chat/9/' }], []);
  assert.equal(items.length, 1);
  assert.equal(items[0].key, 'match:9');
  assert.match(items[0].message, /A match is waiting/);
});

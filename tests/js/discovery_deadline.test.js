const test = require('node:test');
const assert = require('node:assert/strict');
const { matchingDeadlineState, updateDiscoverAvailability } = require('../../plusone/static/plusone/app.js');

const deadline = '2026-10-03T12:00:00.000Z';
const end = Date.parse(deadline);

test('recruiting closes exactly at the server deadline, including a resumed page', () => {
  assert.deepEqual(matchingDeadlineState(deadline, end - 1), { valid: true, matchable: true });
  assert.deepEqual(matchingDeadlineState(deadline, end), { valid: true, matchable: false });
  assert.deepEqual(matchingDeadlineState(deadline, end + 60000), { valid: true, matchable: false });
  assert.deepEqual(matchingDeadlineState('invalid', end), { valid: false, matchable: false });
});

function fixture(disabled = false) {
  const button = { disabled, dataset: {} };
  const notice = { hidden: true };
  const attributes = {};
  const link = { setAttribute: (key, value) => { attributes[key] = value; }, removeAttribute: (key) => { delete attributes[key]; } };
  const card = {
    dataset: { matchingDeadline: deadline },
    querySelectorAll: (selector) => selector === '[data-card-interest]' ? [button] : [link],
    querySelector: () => notice,
  };
  return { button, notice, attributes, container: { querySelectorAll: () => [card] } };
}

test('expired cards keep their button visible but disable it and explain the next step', () => {
  const { button, notice, attributes, container } = fixture();
  updateDiscoverAvailability(end - 1, container);
  assert.equal(button.disabled, false);
  assert.equal(notice.hidden, true);
  updateDiscoverAvailability(end + 5000, container);
  assert.equal(button.disabled, true);
  assert.equal(notice.hidden, false);
  assert.equal(attributes['aria-disabled'], 'true');
  assert.equal(attributes.tabindex, '-1');
});

test('a server clock correction can reopen a client-disabled card without overriding a server pause', () => {
  const ready = fixture();
  updateDiscoverAvailability(end + 5000, ready.container);
  updateDiscoverAvailability(end - 5000, ready.container);
  assert.equal(ready.button.disabled, false);
  assert.equal(ready.attributes['aria-disabled'], undefined);
  const suspended = fixture(true);
  updateDiscoverAvailability(end - 5000, suspended.container);
  assert.equal(suspended.button.disabled, true);
});

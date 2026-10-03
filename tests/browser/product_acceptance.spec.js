const { test, expect } = require('@playwright/test');

function campusTime(minutes) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(Date.now() + minutes * 60_000))
    .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

async function pair(browser, startMinutes = 15) {
  const contexts = [await browser.newContext(), await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
  })];
  const poster = await contexts[0].newPage();
  const guest = await contexts[1].newPage();
  const pageErrors = [];
  for (const page of [poster, guest]) page.on('pageerror', error => pageErrors.push(error.message));
  const title = `Acceptance study ${Date.now().toString().slice(-6)}`;
  await poster.goto('/create/');
  await poster.locator('#id_title').fill(title);
  await poster.locator('#id_description').fill('A campus study session in the isolated test database.');
  await poster.locator('#id_activity_type').selectOption('study');
  await poster.locator('#id_location').selectOption({ label: 'Main Library (Library Quad)' });
  await poster.locator('#id_start_time').fill(campusTime(startMinutes));
  await poster.locator('#id_expected_end_time').fill(campusTime(75));
  await poster.getByRole('button', { name: 'Publish temporary card' }).click();
  await expect(poster).toHaveURL(/\/posts\/\d+\/$/);
  const postUrl = poster.url();
  await guest.goto('/discover/');
  await guest.locator('article.activity-card', { hasText: title }).getByRole('button', { name: 'Interested' }).click();
  await guest.getByRole('link', { name: 'Open chat' }).click();
  await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'waiting');
  return { contexts, poster, guest, title, postUrl, pageErrors, chatUrl: guest.url() };
}

async function startChat(p) {
  await p.poster.goto(p.chatUrl);
  await expect(p.poster.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
  await expect(p.guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
}

async function editPoint(page, point) {
  const details = page.locator('[data-plan-editor]');
  if (await details.getAttribute('open') === null) await details.locator('summary').first().click();
  await page.locator('#meeting-point').fill(point);
}

async function confirmBoth(p) {
  await p.poster.locator('[data-plan-action="confirm_plan"]').click();
  await expect(p.guest.locator('[data-other-agreed]')).toHaveText('yes');
  await p.guest.locator('[data-plan-action="confirm_plan"]').click();
  await expect(p.poster.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'agreed');
  await expect(p.guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'agreed');
}

async function state(page) {
  return page.evaluate(async () => (await fetch(document.querySelector('[data-chat-root]').dataset.chatEndpoint)).json());
}

async function closePair(p) { await Promise.all(p.contexts.map(context => context.close())); }

test('waiting revisit, unsaved-edit choices, changed time and dashboard handoff stay consistent', async ({ browser }) => {
  const p = await pair(browser);
  try {
    const waitDeadline = await p.guest.locator('[data-chat-root]').getAttribute('data-phase-deadline');
    await p.guest.goto('/dashboard/');
    await p.guest.locator('a.match-row-web', { hasText: p.title }).click();
    await expect(p.guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'waiting');
    expect(await p.guest.locator('[data-chat-root]').getAttribute('data-phase-deadline')).toBe(waitDeadline);
    await startChat(p);
    const chatDeadline = await p.poster.locator('[data-chat-root]').getAttribute('data-phase-deadline');
    await editPoint(p.guest, 'Library side lobby');
    await editPoint(p.poster, 'Library north entrance');
    await p.poster.locator('[data-plan-action="update_plan"]').click();
    await expect(p.guest.locator('[data-use-latest-plan]')).toBeVisible();
    await expect(p.guest.locator('#meeting-point')).toHaveValue('Library side lobby');
    await expect(p.guest.locator('[data-plan-action="confirm_plan"]')).toBeDisabled();
    await p.guest.locator('[data-use-latest-plan]').click();
    await expect(p.guest.locator('#meeting-point')).toHaveValue('Library north entrance');
    await expect(p.guest.locator('[data-plan-action="confirm_plan"]')).toBeEnabled();

    await editPoint(p.guest, 'Library courtyard entrance');
    await editPoint(p.poster, 'Library east entrance');
    await p.poster.locator('[data-plan-action="update_plan"]').click();
    await expect(p.guest.locator('[data-keep-plan-edits]')).toBeVisible();
    await p.guest.locator('[data-keep-plan-edits]').click();
    await p.guest.locator('.plan-time-fields > summary').click();
    const meeting = campusTime(10);
    const end = campusTime(65);
    await p.guest.locator('#meeting-at').fill(meeting);
    await p.guest.locator('#meeting-end').fill(end);
    await p.guest.locator('[data-plan-action="update_plan"]').click();
    await expect(p.poster.locator('[data-plan-point]').first()).toHaveText('Library courtyard entrance');
    const current = (await state(p.poster)).plan;
    expect(current.meeting_at_input).toBe(meeting);
    expect(current.expected_end_at_input).toBe(end);
    await expect(p.poster.locator('.chat-room-header [data-plan-time]')).toHaveText(current.meeting_at_display);
    expect(await p.poster.locator('[data-chat-root]').getAttribute('data-phase-deadline')).toBe(chatDeadline);
    await confirmBoth(p);
    await p.guest.goto('/dashboard/');
    const handoff = p.guest.locator('a.match-row-web', { hasText: p.title });
    await expect(handoff).toContainText('Library courtyard entrance');
    await handoff.click();
    await expect(p.guest.locator('[data-chat-compose]')).toBeHidden();
    await expect(p.guest.locator('.chat-room-header [data-plan-time]')).toHaveText(current.meeting_at_display);
    await expect(p.guest.locator('[data-plan-action="outcome"]').first()).toBeDisabled();
    await p.guest.locator('[data-plan-action="arrived"]').click();
    await expect(p.poster.locator('[data-other-meetup-status]')).toContainText('Marked arrived');
    expect((await state(p.poster)).plan.other_outcome).toBe('');
    await p.poster.locator('[data-plan-action="delayed"][value="5"]').click();
    await expect(p.guest.locator('[data-other-meetup-status]')).toContainText('5 min late');
    await p.poster.locator('[data-clear-delay]').click();
    await expect(p.guest.locator('[data-other-meetup-status]')).toContainText('Not marked arrived');
    expect((await state(p.guest)).plan.meeting_at_input).toBe(meeting);
    await p.guest.screenshot({ path: 'test-results/acceptance-revisited-plan-mobile.png', fullPage: true });
    expect(p.pageErrors).toEqual([]);
  } finally { await closePair(p); }
});

test('actual meetup feedback survives response loss and remains independent for each participant', async ({ browser }) => {
  const p = await pair(browser, -1);
  try {
    await startChat(p);
    await editPoint(p.poster, 'Library front steps');
    await p.poster.locator('[data-plan-action="update_plan"]').click();
    await expect(p.guest.locator('[data-plan-point]').first()).toHaveText('Library front steps');
    await confirmBoth(p);
    await p.poster.locator('[data-plan-action="arrived"]').click();
    await expect(p.guest.locator('[data-other-meetup-status]')).toContainText('Marked arrived');
    expect((await state(p.guest)).plan.other_outcome).toBe('');
    let dropped = false;
    await p.poster.route('**/plan/', async route => {
      if (!dropped && (route.request().postData() || '').includes('outcome')) {
        dropped = true;
        expect((await route.fetch()).status()).toBe(200);
        await route.abort('failed');
      } else await route.continue();
    });
    await p.poster.locator('.confirm-meetup-form [data-plan-action="outcome"]').click();
    await expect(p.poster.locator('[data-retry-plan-request]')).toBeVisible();
    await p.poster.reload();
    await expect(p.poster.locator('[data-retry-plan-request]')).toBeVisible();
    await p.poster.locator('[data-retry-plan-request]').click();
    await expect(p.poster.locator('[data-retry-plan-request]')).toBeHidden();
    await expect(p.poster.locator('[data-outcome-recorded]')).toContainText('You reported that you met');
    await expect(p.guest.locator('[data-other-meetup-status]')).toContainText('Reported: we met');
    expect((await state(p.guest)).plan.viewer_outcome).toBe('');
    await p.guest.locator('.not-met-feedback > summary').click();
    await p.guest.locator('#not-met-reason').selectOption('time_conflict');
    await p.guest.locator('.not-met-feedback [data-plan-action="outcome"]').click();
    await expect(p.guest.locator('[data-outcome-recorded]')).toContainText("didn't meet");
    const result = (await state(p.guest)).plan;
    expect(result.viewer_outcome).toBe('not_met');
    expect(result.viewer_outcome_reason).toBe('time_conflict');
    expect(result.other_outcome).toBe('met');
    await p.guest.reload();
    await expect(p.guest.locator('[data-outcome-actions]')).toBeHidden();
    await p.guest.screenshot({ path: 'test-results/acceptance-feedback-mobile.png', fullPage: true });
    expect(p.pageErrors).toEqual([]);
  } finally { await closePair(p); }
});

test('a counterpart identity reset cancels travel instructions and explains the paused original card', async ({ browser }) => {
  const p = await pair(browser);
  try {
    await startChat(p);
    await confirmBoth(p);
    await p.guest.goto('/session/');
    p.guest.once('dialog', dialog => dialog.accept());
    await p.guest.getByRole('button', { name: 'Start fresh identity' }).click();
    await expect(p.poster.locator('[data-meetup-guidance]')).toContainText('cancelled');
    await expect(p.poster.locator('[data-reopen-card]')).toBeVisible();
    await p.poster.goto(p.postUrl);
    await expect(p.poster.getByText('Recruiting is paused', { exact: false })).toBeVisible();
    await p.poster.getByRole('link', { name: 'Review cancelled meetup' }).click();
    await expect(p.poster.getByRole('heading', { name: 'Meetup cancelled.' })).toBeVisible();
    await p.poster.locator('[data-plan-action="reopen_card"]').click();
    await expect(p.poster.locator('[data-reopen-card]')).toBeHidden();
    await p.guest.goto('/discover/');
    await expect(p.guest.locator('article.activity-card', { hasText: p.title })).toBeVisible();
    expect(p.pageErrors).toEqual([]);
  } finally { await closePair(p); }
});

test('existing handoff has a clear no-script boundary and its arrival form works without JavaScript', async ({ browser }) => {
  const p = await pair(browser);
  try {
    await startChat(p);
    await confirmBoth(p);
    const plainContext = await browser.newContext({
      javaScriptEnabled: false, storageState: await p.contexts[0].storageState(),
    });
    p.contexts.push(plainContext);
    const plain = await plainContext.newPage();
    await plain.goto(p.chatUrl);
    // Playwright text selectors exclude noscript descendants, even when scripting is disabled.
    await expect(plain.locator('.chat-js-notice')).toBeVisible();
    await expect(plain.locator('.chat-js-notice')).toContainText('JavaScript is needed for waiting and live chat.');
    await expect(plain.locator('[data-chat-compose]')).toBeHidden();
    await expect(plain.locator('[data-plan-action="outcome"]').first()).toBeDisabled();
    await plain.locator('[data-plan-action="arrived"]').click();
    await expect(plain.locator('[data-viewer-meetup-status]')).toHaveText('Marked arrived');
    await expect(p.guest.locator('[data-other-meetup-status]')).toHaveText('Marked arrived');
    expect((await state(p.guest)).plan.other_outcome).toBe('');
    expect(p.pageErrors).toEqual([]);
  } finally { await closePair(p); }
});

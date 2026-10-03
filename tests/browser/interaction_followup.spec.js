const { test, expect } = require('@playwright/test');

function campusTime(minutes) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(Date.now() + minutes * 60_000))
    .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

async function fillPlan(page, title) {
  await page.locator('#id_title').fill(title);
  await page.locator('#id_description').fill('An isolated interaction regression fixture.');
  await page.locator('#id_activity_type').selectOption('study');
  await page.locator('#id_location').selectOption({ label: 'Main Library (Library Quad)' });
  await page.locator('#id_start_time').fill(campusTime(20));
  await page.locator('#id_expected_end_time').fill(campusTime(80));
  await page.locator('#id_expire_minutes').fill('45');
}

async function requestSuggestion(page) {
  const panel = page.locator('[data-assist-panel]');
  if (await panel.getAttribute('open') === null) await panel.locator('summary').click();
  await page.locator('#id_raw_text').fill('Study tomorrow from 19:00 to 20:30 at the Main Library.');
  await page.getByRole('button', { name: 'Draft my card', exact: true }).click();
  await expect(page.locator('[data-draft-proposal]')).toBeVisible();
}

async function state(page, chatPath) {
  const response = await page.request.get(`${chatPath}messages/`);
  expect(response.ok()).toBe(true);
  return response.json();
}

for (const javaScriptEnabled of [true, false]) {
  test(`Enter preserves manual ownership before and after suggestions, JavaScript ${javaScriptEnabled}`, async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, javaScriptEnabled });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
      const stamp = Date.now().toString().slice(-6);
      await page.goto('/create/');
      await fillPlan(page, `Manual Enter ${stamp}`);
      await page.locator('#id_title').press('Enter');
      await expect(page).toHaveURL(/\/posts\/\d+\/$/);
      await expect(page.getByRole('heading', { name: `Manual Enter ${stamp}`, exact: true })).toBeVisible();

      await page.goto('/create/');
      await fillPlan(page, `Reviewed Enter ${stamp}`);
      await requestSuggestion(page);
      const later = `My later title ${stamp}`;
      await page.locator('#id_title').fill(later);
      await page.locator('#id_title').press('Enter');
      await expect(page).toHaveURL(/\/posts\/\d+\/$/);
      await expect(page.getByRole('heading', { name: later, exact: true })).toBeVisible();
      expect(errors).toEqual([]);
    } finally { await context.close(); }
  });
}

test('refresh after AI uses GET, preserves later edits, and discarding removes old values and suggestions', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  let createPosts = 0;
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/create/' && request.method() === 'POST') createPosts += 1;
  });
  try {
    await page.goto('/create/');
    await fillPlan(page, `Before AI ${Date.now().toString().slice(-6)}`);
    await requestSuggestion(page);
    const later = `Edited after suggestion ${Date.now().toString().slice(-6)}`;
    await page.locator('#id_title').fill(later);
    const postsBeforeReload = createPosts;
    await page.reload();
    await expect(page.locator('#id_title')).toHaveValue(later);
    await expect(page.locator('[data-draft-proposal]')).toBeVisible();
    expect(createPosts).toBe(postsBeforeReload);
    await page.locator('[data-clear-create-draft]').click();
    await expect(page.locator('#id_title')).toHaveValue('');
    await expect(page.locator('[data-draft-proposal]')).toBeHidden();
    await page.reload();
    await expect(page.locator('#id_title')).toHaveValue('');
  } finally { await context.close(); }
});

test('lost publish response reconciles the original card while protecting subsequent edits', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  let release;
  let serverAccepted = false;
  const gate = new Promise(resolve => { release = resolve; });
  try {
    await page.goto('/create/');
    const original = `Uncertain original ${Date.now().toString().slice(-6)}`;
    await fillPlan(page, original);
    await page.route('**/create/**', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      serverAccepted = true;
      await gate;
      await route.abort('failed');
    });
    await page.locator('[data-publish-submit]').click();
    await expect.poll(() => serverAccepted).toBe(true);
    const input = page.locator('#id_title');
    const editableWhileSending = await input.isEnabled() && await input.isEditable();
    const later = `Later unsent edits ${Date.now().toString().slice(-6)}`;
    if (editableWhileSending) await input.fill(later);
    release();
    await expect(page.locator('[data-publish-status]')).toContainText(/lost|unknown|confirm/i);
    await page.unroute('**/create/**');
    await page.reload();
    if (editableWhileSending) {
      await expect(page.locator('#id_title')).toHaveValue(later);
      await page.locator('[data-restore-publish-request]').click();
    } else {
      await expect(page.locator('#id_title')).toHaveValue(original);
      await page.locator('[data-publish-submit]').click();
    }
    await expect(page).toHaveURL(/\/posts\/\d+\/$/);
    await expect(page.getByRole('heading', { name: original, exact: true })).toBeVisible();
    await page.goto('/dashboard/');
    await expect(page.locator('.live-card-row', { hasText: original })).toHaveCount(1);
    if (editableWhileSending) {
      await page.goto('/create/');
      await expect(page.locator('#id_title')).toHaveValue(later);
    }
  } finally { release(); await context.close(); }
});

test('shared plans, focused updates, arrival coordination and unblocked reporting remain consistent', async ({ browser, baseURL }) => {
  const contexts = [await browser.newContext({ baseURL }), await browser.newContext({ baseURL, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })];
  const owner = await contexts[0].newPage();
  const guest = await contexts[1].newPage();
  const errors = [];
  for (const page of [owner, guest]) page.on('pageerror', error => errors.push(error.message));
  try {
    const title = `Followup shared plan ${Date.now().toString().slice(-6)}`;
    await owner.goto('/create/');
    await fillPlan(owner, title);
    await owner.locator('[data-publish-submit]').click();
    await expect(owner).toHaveURL(/\/posts\/\d+\/$/);
    await guest.goto('/discover/');
    await guest.locator('article.activity-card', { hasText: title }).getByRole('button', { name: 'Interested', exact: true }).click();
    const chatPath = await guest.getByRole('dialog').getByRole('link', { name: 'Open chat', exact: true }).getAttribute('href');
    await guest.goto(chatPath);
    await owner.goto('/dashboard/');
    const waitingLink = owner.locator(`[data-match-inbox-list] a[href="${chatPath}"]`);
    await expect(waitingLink).toHaveCount(1);
    await waitingLink.focus();
    await owner.waitForTimeout(5500);
    await expect(waitingLink).toBeFocused();
    await owner.locator('a.match-row-web', { hasText: title }).click();
    await expect(owner.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
    await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
    const chatDeadline = await owner.locator('[data-chat-root]').getAttribute('data-phase-deadline');
    await owner.locator('[data-plan-editor] > summary').click();
    await owner.locator('#meeting-point').fill('Library north entrance');
    await owner.locator('.plan-time-fields > summary').click();
    await owner.locator('#meeting-at').fill(campusTime(15));
    await owner.locator('#meeting-end').fill(campusTime(65));
    await owner.locator('[data-plan-action="update_plan"]').click();
    await expect(guest.locator('[data-shared-plan] > summary')).toContainText('Library north entrance');
    const changed = (await state(guest, chatPath)).plan;
    await expect(guest.locator('.confirmation-summary')).toContainText(changed.expected_end_at_display);
    await expect(guest.locator('[data-shared-plan] > summary')).toContainText(changed.expected_end_at_display);
    expect(await owner.locator('[data-chat-root]').getAttribute('data-phase-deadline')).toBe(chatDeadline);
    await guest.goto('/dashboard/');
    const row = guest.locator('a.match-row-web', { hasText: title });
    await expect(row).toContainText('Library north entrance');
    await expect(row).toContainText(changed.meeting_at_display.replace(/\b0(\d),/g, "$1,"));
    await row.click();
    await owner.locator('[data-plan-action="confirm_plan"]').click();
    await expect(guest.locator('[data-other-agreed]')).toHaveText('yes');
    await guest.locator('[data-plan-action="confirm_plan"]').click();
    await expect(owner.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'agreed');
    await expect(guest.locator('[data-chat-compose]')).toBeHidden();
    await guest.locator('[data-plan-action="coordination"][value="cant_find"]').click();
    await expect(owner.locator('[data-other-meetup-status]')).toContainText(/find/i);
    await owner.locator('[data-plan-action="delayed"][value="5"]').click();
    const arrival = (await state(owner, chatPath)).plan;
    expect(new Date(arrival.viewer_arrival_eta).getTime()).toBeGreaterThan(Date.now());
    await expect(guest.locator('[data-other-meetup-status]')).toContainText(arrival.viewer_arrival_eta_display);
    await expect(guest.locator('[data-other-arrival-updated]')).toContainText(arrival.viewer_arrival_updated_at_display);
    await expect(guest.locator('[data-plan-action="outcome"]').first()).toBeDisabled();
    await guest.getByText('Report a safety issue', { exact: true }).click();
    await expect(guest.locator('[data-report-form]')).toContainText(/cancel/i);
    await guest.locator('#report-category').selectOption('other');
    await guest.locator('#report-reason').fill('An isolated check that optional blocking remains optional.');
    await expect(guest.locator('[data-report-form] [name=block_user]')).not.toBeChecked();
    guest.once('dialog', dialog => dialog.accept());
    await guest.getByRole('button', { name: 'Submit safety report', exact: true }).click();
    await expect(owner.locator('[data-meetup-guidance]')).toContainText(/cancelled/i);
    await guest.goto('/notifications/');
    await expect(guest.getByRole('button', { name: 'Unblock this identity', exact: true })).toHaveCount(0);
    await expect(guest.getByText(title, { exact: true }).first()).toBeVisible();
    expect(errors).toEqual([]);
  } finally { await Promise.all(contexts.map(context => context.close())); }
});

test('a card stops inviting participation when its actual matching deadline passes on screen', async ({ browser, baseURL }) => {
  const contexts = [await browser.newContext({ baseURL }), await browser.newContext({ baseURL })];
  const owner = await contexts[0].newPage();
  const guest = await contexts[1].newPage();
  try {
    const title = `Deadline interaction ${Date.now().toString().slice(-6)}`;
    await owner.goto('/create/');
    await fillPlan(owner, title);
    await owner.locator('[data-publish-submit]').click();
    await expect(owner).toHaveURL(/\/posts\/\d+\/$/);
    await guest.goto('/discover/?activity_type=study&time_window=now');
    const card = guest.locator('article.activity-card', { hasText: title });
    const button = card.getByRole('button', { name: 'Interested', exact: true });
    await expect(button).toBeEnabled();
    const deadline = await card.locator('[data-deadline]').first().getAttribute('data-deadline');
    await guest.clock.setFixedTime(new Date(new Date(deadline).getTime() + 1000));
    await expect(button).toBeDisabled();
    const query = new URL(guest.url()).searchParams;
    expect(query.get('activity_type')).toBe('study');
    expect(query.get('time_window')).toBe('now');
  } finally { await Promise.all(contexts.map(context => context.close())); }
});

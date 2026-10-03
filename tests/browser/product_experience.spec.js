const { test, expect } = require('@playwright/test');

function campusTime(minutes) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: process.env.PLUSONE_CAMPUS_TIME_ZONE || 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(Date.now() + minutes * 60_000))
    .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function recordErrors(page, errors) {
  page.on('pageerror', error => errors.push(error.message));
}

async function openAssist(page) {
  const panel = page.locator('[data-assist-panel]');
  if (await panel.getAttribute('open') === null) await panel.locator('summary').click();
}

async function fillPlan(page, title, type = 'study', location = 'Main Library (Library Quad)') {
  await page.locator('#id_title').fill(title);
  await page.locator('#id_description').fill('An activity used only in the isolated browser acceptance database.');
  await page.locator('#id_activity_type').selectOption(type);
  await page.locator('#id_location').selectOption({ label: location });
  await page.locator('#id_start_time').fill(campusTime(20));
  await page.locator('#id_expected_end_time').fill(campusTime(80));
  await page.locator('#id_expire_minutes').fill('45');
}

async function publish(page, title, type = 'study', location = 'Main Library (Library Quad)') {
  await page.goto('/create/');
  await fillPlan(page, title, type, location);
  await page.locator('[data-publish-submit]').click();
  await expect(page).toHaveURL(/\/posts\/\d+\/$/);
  return page.url();
}

async function expectQueue(page, type = 'study', time = 'now') {
  const query = new URL(page.url()).searchParams;
  expect(query.get('activity_type')).toBe(type);
  expect(query.get('time_window')).toBe(time);
  await expect(page.locator('[name=activity_type]')).toHaveValue(type);
  await expect(page.locator('[name=time_window]')).toHaveValue(time);
}

test('small screens start with manual fields, preserve drafts, and apply AI suggestions only by choice', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const errors = [];
  recordErrors(page, errors);
  try {
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      for (const route of ['/discover/', '/create/']) {
        await page.goto(route);
        const bounds = await page.evaluate(() => ({
          inner: innerWidth, document: document.documentElement.scrollWidth,
          title: document.querySelector('#id_title')?.getBoundingClientRect().toJSON(),
        }));
        expect(bounds.inner).toBe(width);
        expect(bounds.document).toBe(width);
        if (route === '/create/') {
          expect(bounds.title.top).toBeGreaterThanOrEqual(0);
          expect(bounds.title.bottom).toBeLessThan(844);
          expect(await page.locator('[data-assist-panel]').getAttribute('open')).toBeNull();
        } else {
          expect(await page.locator('.discovery-filters').getAttribute('open')).toBeNull();
        }
      }
    }
    const manualTitle = `Reviewed draft ${Date.now().toString().slice(-6)}`;
    await fillPlan(page, manualTitle);
    await expect(page.locator('[data-create-draft-status]')).toContainText('Draft saved');
    await page.goto('/discover/');
    await page.goto('/create/');
    await expect(page.locator('#id_title')).toHaveValue(manualTitle);
    await page.reload();
    await expect(page.locator('#id_title')).toHaveValue(manualTitle);

    await openAssist(page);
    await page.locator('#id_raw_text').fill('Study tomorrow from 19:00 to 20:30 at the Main Library.');
    await page.getByRole('button', { name: 'Draft my card' }).click();
    await expect(page.locator('[data-draft-proposal]')).toBeVisible();
    await expect(page.locator('#id_title')).toHaveValue(manualTitle);
    const editedTitle = `${manualTitle} kept after review`;
    await page.locator('#id_title').fill(editedTitle);
    await page.locator('[data-keep-reviewed-draft]').click();
    await expect(page.locator('[data-draft-proposal]')).toBeHidden();
    await expect(page.locator('#id_title')).toHaveValue(editedTitle);

    await openAssist(page);
    await page.locator('#id_raw_text').fill('Study tomorrow from 19:00 to 20:30 at the Main Library.');
    await page.getByRole('button', { name: 'Draft my card' }).click();
    await expect(page.locator('[data-draft-proposal]')).toBeVisible();
    await expect(page.locator('#id_title')).toHaveValue(editedTitle);
    const proposed = await page.locator('#activity-draft-proposal').evaluate(element => JSON.parse(element.textContent).fields);
    expect(proposed.expected_end_time).toMatch(/T20:30$/);
    await page.locator('[data-apply-draft-proposal]').click();
    await expect(page.locator('[data-draft-proposal]')).toBeHidden();
    await expect(page.locator('#id_title')).toHaveValue(proposed.title);
    await expect(page.locator('#id_start_time')).toHaveValue(proposed.start_time);
    await expect(page.locator('#id_expected_end_time')).toHaveValue(proposed.expected_end_time);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});

test('without JavaScript, drafting and explicit keep/apply retain the current manual form', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  try {
    await page.goto('/create/');
    const manualTitle = `No script reviewed draft ${Date.now().toString().slice(-6)}`;
    await fillPlan(page, manualTitle);
    await openAssist(page);
    await page.locator('#id_raw_text').fill('Study tomorrow from 19:00 to 20:30 at the Main Library.');
    await page.getByRole('button', { name: 'Draft my card' }).click();
    await expect(page.locator('[data-draft-proposal]')).toBeVisible();
    await expect(page.locator('#id_title')).toHaveValue(manualTitle);
    const laterTitle = `${manualTitle} later edit`;
    await page.locator('#id_title').fill(laterTitle);
    await page.locator('[data-keep-reviewed-draft]').click();
    await expect(page.locator('[data-draft-proposal]')).toHaveCount(0);
    await expect(page.locator('#id_title')).toHaveValue(laterTitle);

    await openAssist(page);
    await page.locator('#id_raw_text').fill('Study tomorrow from 19:00 to 20:30 at the Main Library.');
    await page.getByRole('button', { name: 'Draft my card' }).click();
    await expect(page.locator('[data-draft-proposal]')).toBeVisible();
    await expect(page.locator('#id_title')).toHaveValue(laterTitle);
    await page.locator('[data-apply-draft-proposal]').click();
    await expect(page.locator('[data-draft-proposal]')).toHaveCount(0);
    await expect(page.locator('#id_title')).toHaveValue('Study sprint');
    await expect(page.locator('#id_expected_end_time')).toHaveValue(/T20:30$/);
    await expect(page.locator('[data-publish-submit]')).toBeVisible();
  } finally { await context.close(); }
});

test('filtered decisions, public safety, accessible match updates, and two-step reset keep their ownership', async ({ browser, baseURL }) => {
  const contexts = [await browser.newContext({ baseURL }), await browser.newContext({ baseURL, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })];
  const poster = await contexts[0].newPage();
  const guest = await contexts[1].newPage();
  const errors = [];
  for (const page of [poster, guest]) recordErrors(page, errors);
  const stamp = Date.now().toString().slice(-6);
  const studyTitle = `Experience study ${stamp}`;
  const foodTitle = `Experience lunch ${stamp}`;
  const queue = '/discover/?activity_type=study&time_window=now';
  try {
    const studyUrl = await publish(poster, studyTitle);
    await publish(poster, foodTitle, 'food', 'North Dining Hall (North Campus)');
    await guest.goto(queue);
    const studyCard = guest.locator('article.activity-card', { hasText: studyTitle });
    await expect(studyCard).toBeVisible();
    await expect(guest.locator('article.activity-card', { hasText: foodTitle })).toHaveCount(0);
    await studyCard.getByRole('button', { name: 'Pass', exact: true }).click();
    await expectQueue(guest);
    await expect(studyCard).toHaveCount(0);
    await guest.getByRole('button', { name: 'Undo pass', exact: true }).click();
    await expectQueue(guest);
    await expect(studyCard).toBeVisible();

    await studyCard.getByRole('link', { name: 'Details', exact: true }).click();
    const safety = guest.locator('[data-public-safety]');
    await safety.locator('summary').click();
    await guest.locator('#post-report-category').selectOption('other');
    await guest.locator('#post-report-reason').fill('Acceptance check in the isolated test database; no live safety claim.');
    await guest.locator('[data-post-report-form] [name=block_user]').check();
    await guest.getByRole('button', { name: 'Submit card report' }).click();
    await expectQueue(guest);
    await expect(studyCard).toHaveCount(0);
    await guest.goto('/notifications/');
    const reports = guest.locator('article.panel').filter({ has: guest.getByRole('heading', { name: 'My safety reports', exact: true }) });
    await expect(reports).toContainText(studyTitle);
    await expect(reports).toContainText('Pending');
    await guest.getByRole('button', { name: 'Unblock this identity' }).click();
    await expect(guest.getByRole('button', { name: 'Unblock this identity' })).toHaveCount(0);
    await guest.goto(queue);
    await expect(studyCard).toBeVisible();
    await studyCard.getByRole('button', { name: 'Interested', exact: true }).click();
    await expectQueue(guest);
    const dialog = guest.getByRole('dialog');
    await expect(dialog).toBeVisible();
    for (let count = 0; count < 4; count += 1) {
      expect(await guest.evaluate(() => Boolean(document.activeElement?.closest('dialog')))).toBe(true);
      await guest.keyboard.press('Tab');
    }
    const chatUrl = await dialog.getByRole('link', { name: 'Open chat', exact: true }).getAttribute('href');
    await dialog.getByRole('button', { name: 'Close confirmation' }).click();
    await expect(dialog).toBeHidden();
    await guest.goto(studyUrl);
    await expect(guest.getByRole('link', { name: 'Open waiting room', exact: true })).toHaveAttribute('href', chatUrl);
    await guest.goto('/notifications/');
    const waitingUpdate = guest.locator('[data-notification-id^="waiting:"]').first();
    await expect(waitingUpdate).toContainText('A match is waiting');
    await waitingUpdate.getByRole('button', { name: 'Mark read', exact: true }).click();
    await expect(waitingUpdate).not.toHaveClass(/is-unread/);

    const oldTab = await contexts[0].newPage();
    recordErrors(oldTab, errors);
    await oldTab.goto('/create/');
    await fillPlan(oldTab, `Stale unsent card ${stamp}`);
    const oldForm = await oldTab.locator('[data-create-publish-form]').evaluate(form => Object.fromEntries(new FormData(form).entries()));
    const oldScope = await poster.locator('body').getAttribute('data-session-scope');
    await poster.goto('/session/');
    await poster.getByRole('button', { name: 'Start fresh identity', exact: true }).click();
    await expect(poster.getByRole('heading', { name: 'Start a new temporary identity?', exact: true })).toBeVisible();
    expect(await poster.locator('body').getAttribute('data-session-scope')).toBe(oldScope);
    const beforeReset = await guest.request.get(chatUrl.replace(/\/$/, '/messages/'));
    expect((await beforeReset.json()).phase).toBe('waiting');
    await poster.getByRole('button', { name: 'Confirm identity reset', exact: true }).click();
    await expect(poster).toHaveURL(/\/session\/$/);
    expect(await poster.locator('body').getAttribute('data-session-scope')).not.toBe(oldScope);
    await expect(oldTab.locator('[data-publish-submit]')).toBeDisabled();
    await expect(oldTab.getByRole('button', { name: 'Reload current session' })).toBeVisible();

    const csrf = await poster.locator('input[name=csrfmiddlewaretoken]').first().inputValue();
    const staleAttempt = await contexts[0].request.post('/create/', {
      form: { ...oldForm, csrfmiddlewaretoken: csrf, action: 'publish' },
      headers: { Accept: 'application/json' },
    });
    expect(staleAttempt.status()).toBe(409);
    expect((await staleAttempt.json()).identity_changed).toBe(true);
    await poster.goto('/create/');
    await expect(poster.locator('#id_title')).toHaveValue('');
    await poster.goto('/dashboard/');
    await expect(poster.locator('.live-card-row')).toHaveCount(0);
    const afterReset = await guest.request.get(chatUrl.replace(/\/$/, '/messages/'));
    expect((await afterReset.json()).phase).not.toBe('waiting');
    expect(errors).toEqual([]);
  } finally { await Promise.all(contexts.map(context => context.close())); }
});

const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');

function isolatedDatabase() {
  if (!process.env.PLUSONE_E2E_DATABASE_URL) return null;
  const url = new URL(process.env.PLUSONE_E2E_DATABASE_URL);
  if (!['postgres:', 'postgresql:'].includes(url.protocol)
      || !['localhost', '127.0.0.1'].includes(url.hostname)
      || !/_(ci|e2e)$/.test(url.pathname)) throw new Error('Waiting fixtures require an explicitly selected local test database.');
  return url.toString();
}

function expireWaiting(matchId) {
  const database = isolatedDatabase();
  if (!database || !/^\d+$/.test(String(matchId))) throw new Error('No isolated waiting fixture selected.');
  // This fixture advances one test room's server clock, never a production row.
  execFileSync(process.env.PYTHON || 'python', ['-c',
    'import os,sys; os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings"); import django; django.setup(); from datetime import timedelta; from django.utils import timezone; from plusone.models import Match; assert Match.objects.filter(pk=int(sys.argv[1]),status="waiting").update(waiting_expires_at=timezone.now()-timedelta(seconds=1)) == 1',
    String(matchId)], { env: { ...process.env, DATABASE_URL: database, DJANGO_DEBUG: 'true', PLUSONE_MODERATION_MODE: 'rules' } });
}

function campusTime(minutes) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(Date.now() + minutes * 60_000))
    .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

async function publish(page, title) {
  await page.goto('/create/');
  await page.locator('#id_title').fill(title);
  await page.locator('#id_description').fill('A test-only waiting recovery fixture.');
  await page.locator('#id_activity_type').selectOption('study');
  await page.locator('#id_location').selectOption({ label: 'Main Library (Library Quad)' });
  await page.locator('#id_start_time').fill(campusTime(20));
  await page.locator('#id_expected_end_time').fill(campusTime(80));
  await page.locator('#id_expire_minutes').fill('45');
  await page.locator('[data-publish-submit]').click();
  await expect(page).toHaveURL(/\/posts\/\d+\/$/);
}

test('a live timeout offers one explicit new room and replaying the old invite keeps its deadline', async ({ browser, baseURL }) => {
  test.skip(!isolatedDatabase(), 'Server-clock fixtures run only against an explicitly selected local PostgreSQL test database.');
  const contexts = [await browser.newContext({ baseURL }), await browser.newContext({ baseURL })];
  const owner = await contexts[0].newPage();
  const guest = await contexts[1].newPage();
  const errors = [];
  guest.on('pageerror', error => errors.push(error.message));
  try {
    const title = `Waiting recovery ${Date.now().toString(36)}`;
    await publish(owner, title);
    await expect(owner.locator('[aria-label="After publishing"]')).toContainText('My Plus Ones');
    await expect(owner.locator('[aria-label="After publishing"]')).toContainText('My updates');
    await guest.goto('/discover/?activity_type=study&time_window=now');
    await guest.locator('article.activity-card', { hasText: title }).getByRole('button', { name: 'Interested', exact: true }).click();
    const oldPath = await guest.getByRole('dialog').getByRole('link', { name: 'Open chat', exact: true }).getAttribute('href');
    await guest.goto(oldPath);
    await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'waiting');
    const oldId = oldPath.match(/\/chat\/(\d+)\//)[1];
    expireWaiting(oldId);
    await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'expired');
    await expect(guest.locator('[data-waiting-retry-form]')).toBeVisible();
    await guest.locator('[data-waiting-retry-form]').getByRole('button', { name: 'Invite again', exact: true }).click();
    await expect(guest).toHaveURL(/\/chat\/\d+\/$/);
    const nextPath = new URL(guest.url()).pathname;
    expect(nextPath).not.toBe(oldPath);
    await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'waiting');
    const deadline = await guest.locator('[data-chat-root]').getAttribute('data-phase-deadline');
    await guest.goto(oldPath);
    await expect(guest.locator('[data-waiting-retry-form]')).toBeHidden();
    await expect(guest.locator('[data-later-waiting-attempt]')).toHaveAttribute('href', nextPath);
    const replay = await guest.request.post(`${oldPath}retry-waiting/`, {
      form: { csrfmiddlewaretoken: await guest.locator('[name=csrfmiddlewaretoken]').first().inputValue(),
        session_scope: await guest.locator('body').getAttribute('data-session-scope') },
    });
    expect(new URL(replay.url()).pathname).toBe(nextPath);
    await guest.goto(nextPath);
    expect(await guest.locator('[data-chat-root]').getAttribute('data-phase-deadline')).toBe(deadline);
    expect(errors).toEqual([]);
  } finally { await Promise.all(contexts.map(context => context.close())); }
});

test('returning from details keeps discovery filters and its browsing position', async ({ browser, baseURL }) => {
  const contexts = [await browser.newContext({ baseURL }), await browser.newContext({ baseURL, viewport: { width: 390, height: 640 } })];
  const owner = await contexts[0].newPage();
  const guest = await contexts[1].newPage();
  try {
    const stamp = Date.now().toString(36);
    for (let index = 0; index < 3; index += 1) await publish(owner, `Scroll recovery ${stamp} ${index}`);
    await guest.goto('/discover/?activity_type=study&time_window=now');
    const card = guest.locator('article.activity-card', { hasText: `Scroll recovery ${stamp} 2` });
    await card.getByRole('link', { name: 'Details', exact: true }).scrollIntoViewIfNeeded();
    const before = await guest.evaluate(() => window.scrollY);
    expect(before).toBeGreaterThan(200);
    await card.getByRole('link', { name: 'Details', exact: true }).click();
    await guest.getByRole('link', { name: 'Back to Discover', exact: true }).click();
    await expect(guest).toHaveURL(/activity_type=study.*time_window=now/);
    await expect.poll(async () => Math.abs((await guest.evaluate(() => window.scrollY)) - before)).toBeLessThan(60);
    await expect(guest.locator('#filter-activity')).toHaveValue('study');
    await expect(guest.locator('#filter-time')).toHaveValue('now');
  } finally { await Promise.all(contexts.map(context => context.close())); }
});

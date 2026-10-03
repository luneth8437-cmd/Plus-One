const { test, expect } = require('@playwright/test');

function campusTime(minutes) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: process.env.PLUSONE_CAMPUS_TIME_ZONE || 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(Date.now() + minutes * 60_000)).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

async function pair(browser, baseURL, startMinutes = 15) {
  const contexts = [await browser.newContext({ baseURL }), await browser.newContext({ baseURL, viewport: { width: 390, height: 844 }, isMobile: true })];
  const poster = await contexts[0].newPage();
  const guest = await contexts[1].newPage();
  const errors = [];
  for (const page of [poster, guest]) page.on('pageerror', error => errors.push(error.message));
  const title = `Return acceptance ${Date.now().toString().slice(-7)}`;
  await poster.goto('/create/');
  await poster.locator('#id_title').fill(title);
  await poster.locator('#id_description').fill('A fixture in the isolated acceptance database; no production activity.');
  await poster.locator('#id_activity_type').selectOption('study');
  await poster.locator('#id_location').selectOption({ label: 'Main Library (Library Quad)' });
  await poster.locator('#id_start_time').fill(campusTime(startMinutes));
  await poster.locator('#id_expected_end_time').fill(campusTime(75));
  await poster.locator('[data-publish-submit]').click();
  await expect(poster).toHaveURL(/\/posts\/\d+\/$/);
  await guest.goto('/discover/');
  await guest.locator('article.activity-card', { hasText: title }).getByRole('button', { name: 'Interested', exact: true }).click();
  await guest.getByRole('link', { name: 'Open chat', exact: true }).click();
  const chatUrl = guest.url();
  await poster.goto(chatUrl);
  await expect(poster.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
  await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
  return { contexts, poster, guest, chatUrl, title, errors };
}

async function editTimes(page, start, end) {
  const plan = page.locator('[data-plan-editor]');
  if (await plan.getAttribute('open') === null) await plan.locator('summary').first().click();
  const time = page.locator('.plan-time-fields');
  if (await time.getAttribute('open') === null) await time.locator('summary').click();
  await page.locator('#meeting-at').fill(start);
  await page.locator('#meeting-end').fill(end);
}

async function confirmBoth(pair) {
  await pair.poster.locator('[data-plan-action=confirm_plan]').click();
  await expect(pair.guest.locator('[data-other-agreed]')).toHaveText('yes');
  await pair.guest.locator('[data-plan-action=confirm_plan]').click();
  await expect(pair.poster.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'agreed');
  await expect(pair.guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'agreed');
}

async function contrast(page, selector) {
  return page.locator(selector).evaluate(element => {
    const style = getComputedStyle(element);
    const luminance = color => {
      const rgb = color.match(/[\d.]+/g).slice(0, 3).map(value => Number(value) / 255)
        .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
      return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
    };
    const foreground = luminance(style.color), background = luminance(style.backgroundColor);
    return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
  });
}

test('restored earlier plan edits keep valid date bounds through keep-edits and use-latest decisions', async ({ browser, baseURL }) => {
  const p = await pair(browser, baseURL);
  try {
    const earlierStart = campusTime(20), earlierEnd = campusTime(22);
    const laterStart = campusTime(25), laterEnd = campusTime(60);
    await editTimes(p.guest, earlierStart, earlierEnd);
    await editTimes(p.poster, laterStart, laterEnd);
    await p.poster.locator('[data-plan-action=update_plan]').click();
    await expect(p.guest.locator('[data-keep-plan-edits]')).toBeVisible();
    await p.guest.reload();
    await expect(p.guest.locator('#meeting-at')).toHaveValue(earlierStart);
    await expect(p.guest.locator('#meeting-end')).toHaveAttribute('min', earlierStart);
    expect(await p.guest.locator('#meeting-end').evaluate(input => input.validity.rangeUnderflow)).toBe(false);
    await p.guest.locator('[data-keep-plan-edits]').click();
    await expect(p.guest.locator('#meeting-end')).toHaveAttribute('min', earlierStart);
    await expect(p.guest.locator('[data-plan-action=confirm_plan]')).toBeDisabled();

    await p.poster.locator('#meeting-point').fill('Library north public entrance');
    await p.poster.locator('[data-plan-action=update_plan]').click();
    await expect(p.guest.locator('[data-use-latest-plan]')).toBeVisible();
    await p.guest.locator('[data-use-latest-plan]').click();
    await expect(p.guest.locator('#meeting-at')).toHaveValue(laterStart);
    await expect(p.guest.locator('#meeting-end')).toHaveValue(laterEnd);
    await expect(p.guest.locator('#meeting-end')).toHaveAttribute('min', laterStart);
    await expect(p.guest.locator('[data-plan-action=confirm_plan]')).toBeEnabled();
    expect(await contrast(p.guest, '.chat-form button[type=submit]')).toBeGreaterThanOrEqual(4.5);
    expect(await p.guest.locator('[data-phase-countdown]').evaluate(node => node.closest('[aria-live]')?.getAttribute('aria-live'))).toBe('off');
    expect(p.errors).toEqual([]);
  } finally { await Promise.all(p.contexts.map(context => context.close())); }
});

test('own feedback task has a concrete deadline and disappears after the real form records an outcome', async ({ browser, baseURL }) => {
  const p = await pair(browser, baseURL, -1);
  try {
    await confirmBoth(p);
    await p.poster.goto('/dashboard/');
    const task = p.poster.locator('[data-feedback-tasks] a', { hasText: p.title });
    await expect(task).toBeVisible();
    await expect(task).toContainText(/Feedback deadline: [A-Z][a-z]+ \d+, \d\d:\d\d/);
    await expect(p.poster.locator('.dashboard-state-panel')).not.toContainText('All clear');
    await task.click();
    await expect(p.poster).toHaveURL(/#meetup-feedback$/);
    await expect(p.poster.locator('[data-feedback-deadline]')).toHaveText(/[A-Z][a-z]+ \d+, \d\d:\d\d/);
    await p.poster.locator('form[data-meetup-form]:has(input[name=outcome][value=met]) button').click();
    await expect(p.poster.locator('[data-outcome-recorded]')).toContainText('You reported that you met');
    await p.poster.goto('/dashboard/');
    await expect(p.poster.locator('[data-feedback-tasks]')).toHaveCount(0);
    await p.guest.goto('/dashboard/');
    await expect(p.guest.locator('[data-feedback-tasks] a', { hasText: p.title })).toBeVisible();
    expect(p.errors).toEqual([]);
  } finally { await Promise.all(p.contexts.map(context => context.close())); }
});

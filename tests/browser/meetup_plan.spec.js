const { test, expect } = require('@playwright/test');

function campusTime(minutesFromNow) {
  const date = new Date(Date.now() + minutesFromNow * 60_000);
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(date).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

async function apiAction(page, action, revision) {
  return page.evaluate(async ({ action, revision }) => {
    const root = document.querySelector('[data-chat-root]');
    const body = new FormData();
    body.set('csrfmiddlewaretoken', document.querySelector('[name=csrfmiddlewaretoken]').value);
    body.set('session_scope', document.body.dataset.sessionScope);
    body.set('request_id', crypto.randomUUID());
    body.set('revision', revision);
    body.set('action', action);
    const response = await fetch(root.dataset.planEndpoint, {
      method: 'POST', body, headers: { Accept: 'application/json' },
    });
    return { status: response.status, body: await response.json() };
  }, { action, revision });
}

test('shared meetup plan survives response loss, requires current consent, and coordinates arrival and cancellation', async ({ browser }) => {
  const posterContext = await browser.newContext();
  const guestContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const observerContext = await browser.newContext();
  const poster = await posterContext.newPage();
  const guest = await guestContext.newPage();
  const observer = await observerContext.newPage();
  const title = `Meetup coordination ${Date.now().toString().slice(-6)}`;
  try {
    await poster.goto('/create/');
    await poster.locator('#id_title').fill(title);
    await poster.locator('#id_description').fill('An isolated browser test of a campus plan.');
    await poster.locator('#id_activity_type').selectOption('study');
    await poster.locator('#id_location').selectOption({ label: 'Main Library (Library Quad)' });
    await poster.locator('#id_start_time').fill(campusTime(15));
    await poster.locator('#id_expected_end_time').fill(campusTime(75));
    await poster.getByRole('button', { name: 'Publish temporary card' }).click();
    await expect(poster).toHaveURL(/\/posts\/\d+\/$/);

    await guest.goto('/discover/');
    await guest.locator('article.activity-card', { hasText: title }).getByRole('button', { name: 'Interested' }).click();
    await guest.getByRole('link', { name: 'Open chat' }).click();
    await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'waiting');
    await expect(guest.locator('[data-chat-compose]')).toBeHidden();
    await expect(guest.locator('[data-waiting-notice]')).toBeVisible();
    await guest.screenshot({ path: 'test-results/meetup-waiting-mobile.png', fullPage: true });

    await poster.goto(guest.url());
    await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
    await expect(poster.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'chatting');
    const deadline = await poster.locator('[data-chat-root]').getAttribute('data-phase-deadline');
    await poster.locator('[data-plan-editor]').evaluate(node => { node.open = true; });
    await poster.locator('#meeting-point').fill('Library east entrance');

    let droppedRequest;
    let replayedRequest;
    await poster.route('**/plan/', async route => {
      const request = route.request();
      // Multipart submissions are also identified by their literal action.
      const data = request.postData() || '';
      if (request.method() === 'POST' && data.includes('update_plan') && !droppedRequest) {
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        droppedRequest = data;
        await route.abort('failed');
        return;
      }
      if (request.method() === 'POST' && data.includes('update_plan') && droppedRequest) replayedRequest = data;
      await route.continue();
    });
    await poster.locator('[data-plan-edit-form] [data-plan-action="update_plan"]').click();
    await expect(poster.locator('[data-retry-plan-request]')).toBeVisible();
    await poster.reload();
    await expect(poster.locator('[data-retry-plan-request]')).toBeVisible();
    await poster.locator('[data-retry-plan-request]').click();
    await expect(poster.locator('[data-retry-plan-request]')).toBeHidden();
    expect(droppedRequest).toBeTruthy();
    expect(replayedRequest).toBeTruthy();
    const requestId = body => {
      const match = body.match(/name="request_id"\r?\n\r?\n([^\r\n]+)/);
      return match ? match[1] : new URLSearchParams(body).get('request_id');
    };
    expect(requestId(replayedRequest)).toBe(requestId(droppedRequest));
    await poster.unroute('**/plan/');
    await expect(guest.locator('[data-plan-point]').first()).toHaveText('Library east entrance');
    expect(await poster.locator('[data-chat-root]').getAttribute('data-phase-deadline')).toBe(deadline);

    await poster.locator('[data-plan-action="confirm_plan"]').click();
    await expect(guest.locator('[data-other-agreed]')).toHaveText('yes');
    const oldRevision = await guest.locator('[data-plan-action="confirm_plan"]').locator('xpath=ancestor::form').locator('[name=revision]').inputValue();
    await guest.locator('[data-plan-editor]').evaluate(node => { node.open = true; });
    await guest.locator('#meeting-point').fill('Library west entrance');
    await guest.locator('[data-plan-edit-form] [data-plan-action="update_plan"]').click();
    await expect(poster.locator('[data-plan-point]').first()).toHaveText('Library west entrance');
    await expect(poster.locator('[data-viewer-agreed]')).toHaveText('no');
    const stale = await apiAction(poster, 'confirm_plan', oldRevision);
    expect(stale.status).toBe(409);
    expect(stale.body.plan.meeting_point).toBe('Library west entrance');

    await poster.locator('[data-plan-action="confirm_plan"]').click();
    await expect(guest.locator('[data-other-agreed]')).toHaveText('yes');
    await guest.locator('[data-plan-action="confirm_plan"]').click();
    await expect(guest.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'agreed');
    await expect(poster.locator('[data-chat-root]')).toHaveAttribute('data-chat-status', 'agreed');
    await expect(guest.locator('[data-chat-compose]')).toBeHidden();
    await guest.locator('[data-plan-action="arrived"]').click();
    await expect(poster.locator('[data-other-meetup-status]')).toContainText('At the agreed meeting point');
    await poster.locator('[data-plan-action="delayed"][value="10"]').click();
    const arrival = await poster.evaluate(async () => (await fetch(document.querySelector('[data-chat-root]').dataset.chatEndpoint)).json());
    expect(new Date(arrival.plan.viewer_arrival_eta).getTime()).toBeGreaterThan(Date.now());
    await expect(guest.locator('[data-other-meetup-status]')).toContainText(arrival.plan.viewer_arrival_eta_display);
    await guest.screenshot({ path: 'test-results/meetup-handoff-mobile.png', fullPage: true });

    guest.once('dialog', dialog => dialog.accept());
    await guest.locator('[data-plan-action="cancel_meetup"]').click();
    await expect(poster.locator('[data-meetup-guidance]')).toContainText(/cancelled/i);
    await observer.goto('/discover/');
    await expect(observer.locator('article.activity-card', { hasText: title })).toHaveCount(0);
    await poster.locator('[data-plan-action="reopen_card"]').click();
    await expect(poster.locator('[data-reopen-card]')).toBeHidden();
    await observer.reload();
    await expect(observer.locator('article.activity-card', { hasText: title })).toBeVisible();
  } finally {
    await Promise.all([posterContext.close(), guestContext.close(), observerContext.close()]);
  }
});

const { test, expect } = require("@playwright/test");

function shanghaiDateTime(hoursFromNow = 3) {
  const date = new Date(Date.now() + hoursFromNow * 60 * 60 * 1000);
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

test("two isolated users complete a reliable waiting, chat, replay, agreement, and report flow", async ({ browser, baseURL }) => {
  const posterContext = await browser.newContext();
  const joinerContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const poster = await posterContext.newPage();
  const joiner = await joinerContext.newPage();
  const title = `Playwright badminton ${Date.now().toString().slice(-6)}`;

  try {
    await poster.goto("/discover/");
    await poster.goto("/create/");
    await expect(poster.locator("[data-publish-status]")).toBeHidden();
    await expect(poster.locator("[data-publish-recovery-actions]")).toBeHidden();
    await poster.locator("#id_title").fill(title);
    await poster.locator("#id_description").fill("A local browser reliability test for a casual campus game.");
    await poster.locator("#id_activity_type").selectOption("sports");
    await poster.locator("#id_location").selectOption({ label: "Campus Sports Hall (Central Campus)" });
    await poster.locator("#id_start_time").fill(shanghaiDateTime());
    await expect(poster.locator("#id_expected_end_time")).toBeVisible();
    await poster.locator("#id_expected_end_time").fill(shanghaiDateTime(4));
    await poster.locator("#id_expire_minutes").fill("45");
    await poster.getByRole("button", { name: "Publish temporary card" }).click();
    await expect(poster).toHaveURL(/\/posts\/\d+\/$/);
    await expect(poster.getByRole("heading", { name: title })).toBeVisible();
    await expect(poster.locator(".chips")).toContainText("Ends");

    await joiner.goto("/discover/");
    const card = joiner.locator("article.activity-card", { hasText: title });
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Interested" }).click();
    await expect(joiner.getByRole("heading", { name: "It's a vibe." })).toBeVisible();
    await joiner.getByRole("link", { name: "Open chat" }).click();

    const joinerRoot = joiner.locator("[data-chat-root]");
    await expect(joiner.locator("[data-chat-sync-status]")).toBeHidden();
    await expect(joinerRoot).toHaveAttribute("data-chat-status", "waiting");
    await expect(joiner.locator("[data-chat-form] input[name='message']")).toBeDisabled();
    await expect(joiner.locator("[data-chat-action='agree']")).toBeDisabled();
    await expect(joiner.locator("[data-chat-action='ai']")).toBeDisabled();
    await expect(joiner.getByRole("button", { name: "Decline match" })).toBeEnabled();
    await expect(joiner.getByText("Report a safety issue")).toBeVisible();

    await poster.goto("/dashboard/");
    await expect(poster.getByText(title).first()).toBeVisible();
    await poster.locator("a.match-row-web", { hasText: title }).first().click();
    const posterRoot = poster.locator("[data-chat-root]");

    await expect(posterRoot).toHaveAttribute("data-chat-status", "chatting", { timeout: 15_000 });
    await expect(joinerRoot).toHaveAttribute("data-chat-status", "chatting", { timeout: 15_000 });
    const remainingSeconds = await posterRoot.evaluate((node) => {
      return (new Date(node.dataset.phaseDeadline).getTime() - Date.now()) / 1000;
    });
    expect(remainingSeconds).toBeGreaterThan(240);
    expect(remainingSeconds).toBeLessThanOrEqual(310);

    const posterInput = poster.locator("[data-chat-form] input[name='message']");
    await posterInput.fill("Hello from the poster");
    await poster.getByRole("button", { name: "Send" }).click();
    await expect(joiner.locator(".bubble p", { hasText: "Hello from the poster" })).toHaveCount(1);

    const joinerInput = joiner.locator("[data-chat-form] input[name='message']");
    await joinerInput.scrollIntoViewIfNeeded();
    await joinerInput.focus();
    await joiner.keyboard.type("Reply sent with the mobile keyboard");
    await joiner.keyboard.press("Enter");
    await expect(poster.locator(".bubble p", { hasText: "Reply sent with the mobile keyboard" })).toHaveCount(1);
    await joinerRoot.screenshot({ path: "test-results/mobile-chat.png" });

    const raceText = "GET confirms before the POST response fails";
    let raceResponseAborted = false;
    await poster.route("**/messages/**", async (route) => {
      const request = route.request();
      if (request.method() === "POST" && (request.postData() || "").includes(raceText)) {
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        await new Promise((resolve) => setTimeout(resolve, 2500));
        raceResponseAborted = true;
        await route.abort("failed");
        return;
      }
      await route.continue();
    });
    await posterInput.fill(raceText);
    await poster.getByRole("button", { name: "Send" }).click();
    await expect(poster.locator(".bubble p", { hasText: raceText })).toHaveCount(1);
    await expect.poll(() => raceResponseAborted).toBe(true);
    await expect(poster.locator("[data-chat-draft-recovery]")).toBeHidden();
    await expect(poster.locator("[data-chat-warning]")).not.toContainText(/result is unknown/i);
    await poster.unroute("**/messages/**");

    const lostText = "Message whose first response is lost";
    const lostRequestId = await poster.locator("[data-chat-form] [name='request_id']").inputValue();
    let dropFirstResponse = true;
    let blockPoll = true;
    await poster.route("**/messages/**", async (route) => {
      const request = route.request();
      if (request.method() === "GET" && blockPoll) {
        await route.abort("failed");
        return;
      }
      if (request.method() === "POST" && dropFirstResponse && (request.postData() || "").includes(lostText)) {
        dropFirstResponse = false;
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        await route.abort("failed");
        return;
      }
      await route.continue();
    });

    await posterInput.fill(lostText);
    await poster.getByRole("button", { name: "Send" }).click();
    await expect(poster.locator("[data-chat-warning]")).toContainText(/result is unknown/i);
    await expect(poster.locator("[data-chat-sync-status]")).toHaveText("Connection interrupted. Retrying…");
    await expect(poster.locator("[data-chat-draft-recovery]")).toBeVisible();
    await expect(poster.locator("[data-chat-recovery-text]")).toHaveValue(lostText);
    blockPoll = false;
    await poster.getByRole("button", { name: "Check the same request again" }).click();
    await expect(poster.locator(".bubble p", { hasText: lostText })).toHaveCount(1);
    await expect(poster.locator("[data-chat-sync-status]")).toBeHidden();
    await expect(poster.locator("[data-chat-draft-recovery]")).toBeHidden();

    const conflictStatus = await poster.evaluate(async ({ requestId, message }) => {
      const root = document.querySelector("[data-chat-root]");
      const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
      const body = new FormData();
      body.set("csrfmiddlewaretoken", csrf);
      body.set("request_id", requestId);
      body.set("message", message);
      const response = await fetch(root.dataset.chatEndpoint, { method: "POST", body, headers: { Accept: "application/json" } });
      return response.status;
    }, { requestId: lostRequestId, message: "Different payload for the same request ID" });
    expect(conflictStatus).toBe(409);
    await expect(poster.locator(".bubble p", { hasText: "Different payload for the same request ID" })).toHaveCount(0);

    await poster.unroute("**/messages/**");
    const closedLostText = "Accepted before the chat closes";
    const closedRequestId = await poster.locator("[data-chat-form] [name='request_id']").inputValue();
    let dropClosedResponse = true;
    await poster.route("**/messages/**", async (route) => {
      const request = route.request();
      if (request.method() === "GET") {
        await route.abort("failed");
        return;
      }
      if (request.method() === "POST" && dropClosedResponse && (request.postData() || "").includes(closedLostText)) {
        dropClosedResponse = false;
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        await route.abort("failed");
        return;
      }
      await route.continue();
    });
    await posterInput.fill(closedLostText);
    await poster.getByRole("button", { name: "Send" }).click();
    await expect(poster.locator("[data-chat-warning]")).toContainText(/result is unknown/i);

    const posterAgreeStatus = await poster.evaluate(async () => {
      const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
      const body = new FormData();
      body.set("csrfmiddlewaretoken", csrf);
      body.set("action", "agree");
      const response = await fetch(window.location.href, { method: "POST", body, redirect: "manual" });
      return response.status;
    });
    expect([0, 302]).toContain(posterAgreeStatus);
    await expect(joiner.locator("[data-other-agreed]")).toHaveText("yes", { timeout: 10_000 });
    await joiner.locator("[data-chat-action='agree']").click();
    await expect(joiner.locator("[data-chat-root]")).toHaveAttribute("data-chat-status", "agreed");
    await expect(joiner.getByRole("heading", { name: "Meet handoff ready." })).toBeVisible();

    await poster.getByRole("button", { name: "Check the same request again" }).click();
    await expect(poster.locator(".bubble p", { hasText: closedLostText })).toHaveCount(1);
    await expect(poster.locator("[data-chat-draft-recovery]")).toBeHidden();
    await poster.unroute("**/messages/**");

    await poster.evaluate(({ requestId, text }) => {
      const matchId = document.querySelector("[data-chat-root]").dataset.chatId;
      sessionStorage.setItem(`plusone:chat-draft:${matchId}`, JSON.stringify({ requestId, text, state: "unknown" }));
    }, { requestId: closedRequestId, text: closedLostText });
    await poster.reload();
    await expect(poster.locator("[data-chat-root]")).toHaveAttribute("data-chat-status", "agreed");
    await expect(poster.locator(".bubble p", { hasText: closedLostText })).toHaveCount(1);
    await expect(poster.locator("[data-chat-draft-recovery]")).toBeHidden();
    const recoveredStorage = await poster.evaluate(() => {
      const matchId = document.querySelector("[data-chat-root]").dataset.chatId;
      return sessionStorage.getItem(`plusone:chat-draft:${matchId}`);
    });
    expect(recoveredStorage).toBeNull();

    await joiner.getByText("Report a safety issue").click();
    await joiner.locator("#report-category").selectOption("unsafe_meeting");
    await joiner.locator("#report-reason").fill("Browser regression report after the handoff ended.");
    joiner.once("dialog", (dialog) => dialog.accept());
    await joiner.getByRole("button", { name: "Submit safety report" }).click();
    await expect(joiner.getByText("Safety report recorded.", { exact: false })).toBeVisible();
    await expect(joiner.locator("[data-chat-root]")).toHaveAttribute("data-chat-status", "agreed");
  } finally {
    await posterContext.close().catch(() => {});
    await joinerContext.close().catch(() => {});
  }
});

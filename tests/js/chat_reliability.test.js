const test = require("node:test");
const assert = require("node:assert/strict");

const {
  advanceCursor,
  applyPollPayload,
  appendChatMessage,
  createPollLoop,
  fetchWithTimeout,
  findRecoveredMessage,
  hasRenderedRequestId,
  restoreFormPayload,
} = require("../../plusone/static/plusone/app.js");

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.className = "";
    this.textContent = "";
    this.scrollHeight = 0;
    this.scrollTop = 0;
  }

  append(...nodes) {
    this.children.push(...nodes);
    this.scrollHeight = this.children.length;
  }

  insertBefore(node, reference) {
    const index = this.children.indexOf(reference);
    if (index === -1) this.append(node);
    else this.children.splice(index, 0, node);
  }

  remove() {
    this.removed = true;
  }

  querySelectorAll(selector) {
    if (selector === "[data-message-id]") {
      return this.children.filter((child) => child.dataset.messageId !== undefined);
    }
    if (selector === "[data-request-id]") {
      return this.children.filter((child) => child.dataset.requestId !== undefined);
    }
    return [];
  }

  querySelector(selector) {
    if (selector === "[data-empty-chat]") return null;
    return null;
  }
}

global.document = {
  createElement: (tagName) => new FakeElement(tagName),
};

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("POST rendering never advances the GET cursor", () => {
  const list = new FakeElement();
  list.dataset.lastMessageId = "10";

  assert.equal(appendChatMessage(list, { id: 12, message: "mine", sender_label: "You" }), true);
  assert.equal(list.dataset.lastMessageId, "10");
  assert.equal(advanceCursor(10, 9), 10);
});

test("GET payload is sorted, deduplicated, and advances cursor monotonically", () => {
  const list = new FakeElement();
  appendChatMessage(list, { id: 12, message: "POST response" });
  let cursor = applyPollPayload(list, {
    messages: [
      { id: 12, message: "third" },
      { id: 11, message: "second" },
      { id: 11, message: "duplicate" },
    ],
    next_cursor: 12,
  }, 10);

  assert.deepEqual(list.children.map((child) => child.dataset.messageId), ["11", "12"]);
  assert.equal(cursor, 12);

  cursor = applyPollPayload(list, { messages: [{ id: 11, message: "late duplicate" }], next_cursor: 9 }, cursor);
  assert.equal(list.children.length, 2);
  assert.equal(cursor, 12);
});

test("poll loop allows one in-flight request", async () => {
  let calls = 0;
  let finish;
  const task = () => {
    calls += 1;
    return new Promise((resolve) => { finish = resolve; });
  };
  const scheduled = [];
  const loop = createPollLoop({
    task,
    isVisible: () => true,
    schedule: (callback, delay) => { scheduled.push({ callback, delay }); return scheduled.length; },
    cancel: () => {},
  });

  loop.start();
  await settle();
  assert.equal(calls, 1);
  assert.equal(loop.isInFlight(), true);
  assert.equal(await loop.trigger(), false);
  assert.equal(calls, 1);
  finish();
  await settle();
  assert.equal(scheduled.at(-1).delay, 2000);
  loop.stop();
});

test("poll loop stops while hidden and backs off after repeated failures", async () => {
  let visible = false;
  let calls = 0;
  const scheduled = [];
  const loop = createPollLoop({
    task: async () => { calls += 1; throw new Error("offline"); },
    isVisible: () => visible,
    schedule: (callback, delay) => { scheduled.push({ callback, delay }); return scheduled.length; },
    cancel: () => {},
    baseDelay: 2000,
    maxDelay: 16000,
  });

  loop.start();
  await settle();
  assert.equal(calls, 0);

  visible = true;
  loop.start();
  await settle();
  assert.equal(calls, 1);
  assert.equal(scheduled.at(-1).delay, 2000);

  await scheduled.at(-1).callback();
  await settle();
  assert.equal(calls, 2);
  assert.equal(scheduled.at(-1).delay, 4000);
  loop.stop();
});

test("unknown send recovery matches request ID, not repeated text", () => {
  const messages = [
    { id: 21, message: "same text", sender_label: "You", request_id: "request-a" },
    { id: 22, message: "same text", sender_label: "You", request_id: "request-b" },
  ];

  assert.equal(findRecoveredMessage(messages, "request-b").id, 22);
  assert.equal(findRecoveredMessage(messages, "missing"), null);
});

test("a GET-confirmed request stays confirmed when its delayed POST fails", () => {
  const list = new FakeElement();
  appendChatMessage(list, { id: 23, message: "confirmed by poll", sender_label: "You", request_id: "request-race" });

  assert.equal(hasRenderedRequestId(list, "request-race"), true);
  assert.equal(hasRenderedRequestId(list, "request-other"), false);
});

test("bounded fetch aborts a stalled request", async () => {
  const stalledFetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => {
      const error = new Error("aborted");
      error.name = "AbortError";
      reject(error);
    }, { once: true });
  });

  await assert.rejects(fetchWithTimeout("/slow", {}, 5, stalledFetch), { name: "AbortError" });
});

test("unknown publish payload restores values after refresh", () => {
  const elements = [
    { name: "title", type: "text", value: "" },
    { name: "confirm_date", type: "checkbox", value: "yes", checked: false },
    { name: "request_id", type: "hidden", value: "keep-this-id" },
  ];
  const form = { elements };
  const payload = JSON.stringify([["confirm_date", "yes"], ["title", "Restored plan"]]);

  assert.equal(restoreFormPayload(form, payload), true);
  assert.equal(elements[0].value, "Restored plan");
  assert.equal(elements[1].checked, true);
  assert.equal(elements[2].value, "keep-this-id");
});

(() => {
  "use strict";

  let serverClockOffsetMs = 0;

  function setServerClock(serverTime) {
    if (!serverTime) return;
    const parsed = new Date(serverTime).getTime();
    if (!Number.isNaN(parsed)) serverClockOffsetMs = parsed - Date.now();
  }

  function updateCountdowns() {
    const now = Date.now() + serverClockOffsetMs;
    document.querySelectorAll(".countdown[data-deadline]").forEach((node) => {
      const deadline = new Date(node.dataset.deadline).getTime();
      const delta = deadline - now;
      if (Number.isNaN(deadline)) {
        node.textContent = node.dataset.fallback || "closed";
        return;
      }
      if (delta <= 0) {
        node.textContent = "expired";
        node.classList.add("expired");
        node.classList.remove("urgent");
        if (node.hasAttribute("data-phase-countdown")) {
          const root = node.closest("[data-chat-root]");
          if (root && ["waiting", "chatting"].includes(root.dataset.chatStatus)) {
            root.dataset.chatActive = "false";
            root.querySelector("[data-phase-guidance]").textContent = "Time is up. Checking the final match status…";
            root.querySelectorAll("[data-plan-action='update_plan'], [data-plan-action='confirm_plan']")
              .forEach((button) => { button.disabled = true; });
            syncChatSendControls(root, root.dataset.chatSendBusy === "true");
          }
        }
        return;
      }
      const totalSeconds = Math.floor(delta / 1000);
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      node.textContent = `${minutes}m ${String(seconds).padStart(2, "0")}s`;
      node.classList.toggle("urgent", totalSeconds <= 60);
      node.classList.remove("expired");
    });
  }

  function createPollLoop({ task, isVisible, schedule = setTimeout, cancel = clearTimeout, baseDelay = 2000, maxDelay = 16000 }) {
    let running = false;
    let inFlight = false;
    let timer = null;
    let failures = 0;

    function clearTimer() {
      if (timer !== null) cancel(timer);
      timer = null;
    }

    function scheduleNext() {
      if (!running || !isVisible()) return;
      const delay = failures ? Math.min(maxDelay, baseDelay * (2 ** (failures - 1))) : baseDelay;
      clearTimer();
      timer = schedule(trigger, delay);
    }

    async function trigger() {
      clearTimer();
      if (!running || !isVisible() || inFlight) return false;
      inFlight = true;
      try {
        await task();
        failures = 0;
      } catch (_error) {
        failures += 1;
      } finally {
        inFlight = false;
        scheduleNext();
      }
      return true;
    }

    function start() {
      if (!running) running = true;
      trigger();
    }

    function stop() {
      running = false;
      clearTimer();
    }

    return { start, stop, trigger, isInFlight: () => inFlight, isRunning: () => running };
  }

  function fetchWithTimeout(input, options = {}, timeoutMs = 10000, fetchImpl = globalThis.fetch) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    if (options.signal) {
      if (options.signal.aborted) controller.abort();
      else options.signal.addEventListener("abort", () => controller.abort(), { once: true });
    }
    return fetchImpl(input, { ...options, signal: controller.signal })
      .finally(() => clearTimeout(timer));
  }

  function numericId(value) {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function advanceCursor(current, next) {
    return Math.max(numericId(current), numericId(next));
  }

  function findMessage(list, id) {
    return Array.from(list.querySelectorAll("[data-message-id]")).find((node) => String(node.dataset.messageId) === String(id));
  }

  function appendChatMessage(list, message, options = {}) {
    if (!message?.id || findMessage(list, message.id)) return false;
    list.querySelector("[data-empty-chat]")?.remove();

    const bubble = document.createElement("div");
    bubble.className = `bubble ${message.bubble_class || "theirs"}`;
    bubble.dataset.messageId = String(message.id);
    if (message.request_id) bubble.dataset.requestId = String(message.request_id);

    const meta = document.createElement("small");
    meta.textContent = `${message.sender_label || "Anonymous match"} · ${message.created_at || "now"}${message.is_flagged ? " · flagged" : ""}`;
    const text = document.createElement("p");
    text.textContent = message.message || "";
    bubble.append(meta, text);
    const next = Array.from(list.querySelectorAll("[data-message-id]"))
      .find((node) => numericId(node.dataset.messageId) > numericId(message.id));
    if (next) list.insertBefore(bubble, next);
    else list.append(bubble);
    if (options.scroll !== false) list.scrollTop = list.scrollHeight;
    return true;
  }

  function applyPollPayload(list, payload, currentCursor) {
    const messages = Array.isArray(payload?.messages) ? [...payload.messages] : [];
    messages.sort((left, right) => numericId(left.id) - numericId(right.id));
    messages.forEach((message) => appendChatMessage(list, message));
    const responseCursor = payload?.next_cursor ?? messages.reduce((max, message) => Math.max(max, numericId(message.id)), currentCursor);
    return advanceCursor(currentCursor, responseCursor);
  }

  function findRecoveredMessage(messages, requestId) {
    if (!requestId || !Array.isArray(messages)) return null;
    return messages.find((message) => message.request_id && String(message.request_id) === String(requestId)) || null;
  }

  function hasRenderedRequestId(list, requestId) {
    if (!list || !requestId) return false;
    return Array.from(list.querySelectorAll("[data-request-id]"))
      .some((message) => message.dataset.requestId === String(requestId));
  }

  function restoreFormPayload(form, payload) {
    let entries;
    try { entries = JSON.parse(payload || "[]"); } catch (_error) { return false; }
    if (!Array.isArray(entries)) return false;
    const names = new Set(entries.map(([name]) => name));
    Array.from(form.elements).forEach((control) => {
      if (!control.name || control.name === "csrfmiddlewaretoken" || control.name === "request_id") return;
      if ((control.type === "checkbox" || control.type === "radio") && !names.has(control.name)) control.checked = false;
    });
    entries.forEach(([name, value]) => {
      const controls = Array.from(form.elements).filter((control) => control.name === name);
      controls.forEach((control) => {
        if (control.type === "checkbox" || control.type === "radio") control.checked = control.value === value;
        else control.value = value;
      });
    });
    return true;
  }

  function createUuid() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
      const random = Math.floor(Math.random() * 16);
      return (character === "x" ? random : (random & 0x3) | 0x8).toString(16);
    });
  }

  function readSessionJson(key) {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "null");
    } catch (_error) {
      return null;
    }
  }

  function writeSessionJson(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, JSON.stringify(value));
    } catch (_error) {
      // Private browsing or a full storage quota must not block the action.
    }
  }

  function previewLocationLabel(locationInput) {
    if (!locationInput?.value) return "Campus location";
    const label = locationInput.selectedOptions?.[0]?.textContent?.trim();
    return label && label !== "---------" ? label : "Campus location";
  }

  function previewActivityLabel(activityInput) {
    if (!activityInput?.value) return "Activity";
    const label = activityInput.selectedOptions?.[0]?.textContent?.trim();
    return label && label !== "---------" ? label : "Activity";
  }

  function previewStartTimeLabel(value) {
    if (!value) return "Start time";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Start time";
    const month = date.toLocaleString(undefined, { month: "short" });
    return `${month} ${date.getDate()}, ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  }

  function updateCreatePreview() {
    const titleInput = document.getElementById("id_title");
    const descriptionInput = document.getElementById("id_description");
    const activityInput = document.getElementById("id_activity_type");
    const locationInput = document.getElementById("id_location");
    const startInput = document.getElementById("id_start_time");
    const endInput = document.getElementById("id_expected_end_time");
    const expireInput = document.getElementById("id_expire_minutes");
    const previewTitle = document.querySelector("[data-post-preview-title]");
    if (!previewTitle) return;

    previewTitle.textContent = titleInput?.value || "Your Plus One title";
    document.querySelector("[data-post-preview-description]").textContent = descriptionInput?.value || "The card preview updates as you edit the structured fields.";
    document.querySelector("[data-post-preview-activity]").textContent = previewActivityLabel(activityInput);
    document.querySelector("[data-post-preview-location]").textContent = previewLocationLabel(locationInput);
    document.querySelector("[data-post-preview-time]").textContent = previewStartTimeLabel(startInput?.value);
    const previewEnd = document.querySelector("[data-post-preview-end]");
    if (previewEnd) previewEnd.textContent = endInput?.value ? `Ends ${previewStartTimeLabel(endInput.value)}` : "Expected end time";
    document.querySelector("[data-post-preview-expire]").textContent = expireInput?.value || "45";
    const previewCard = previewTitle.closest(".activity-card");
    if (previewCard) {
      previewCard.classList.remove("color-food", "color-sports", "color-study", "color-club", "color-explore", "color-other");
      previewCard.classList.add(`color-${activityInput?.value || "other"}`);
    }
  }

  function setupTimeClarifier() {
    const startInput = document.getElementById("id_start_time");
    if (!startInput) return;
    document.querySelectorAll("[data-time-option]").forEach((button) => {
      button.addEventListener("click", () => {
        startInput.value = button.dataset.timeOption || "";
        document.querySelectorAll("[data-time-option]").forEach((option) => option.classList.remove("is-selected"));
        button.classList.add("is-selected");
        startInput.dispatchEvent(new Event("input", { bubbles: true }));
        startInput.dispatchEvent(new Event("change", { bubbles: true }));
        startInput.focus();
      });
    });
  }

  function setupReliablePublish() {
    const form = document.querySelector("[data-create-publish-form]");
    if (!form) return;
    const requestInput = form.querySelector("[data-request-id]");
    const submit = form.querySelector("[data-publish-submit]");
    const status = form.querySelector("[data-publish-status]");
    const recoveryActions = form.querySelector("[data-publish-recovery-actions]");
    const restoreButton = form.querySelector("[data-restore-publish-request]");
    const newButton = form.querySelector("[data-new-publish-request]");
    const storageKey = "plusone:create-request";
    let stored = readSessionJson(storageKey);
    if (stored?.state === "sending") stored.state = "unknown";
    requestInput.value = stored?.requestId || requestInput.value || createUuid();
    stored = { ...stored, requestId: requestInput.value, state: stored?.state || "draft" };

    function payloadSnapshot() {
      return JSON.stringify(Array.from(new FormData(form).entries())
        .filter(([name]) => !["csrfmiddlewaretoken", "request_id"].includes(name))
        .sort(([left], [right]) => left.localeCompare(right)));
    }

    function fingerprintSnapshot() {
      return JSON.stringify(Array.from(new FormData(form).entries())
        .filter(([name]) => !["csrfmiddlewaretoken", "request_id", "confirm_date"].includes(name))
        .sort(([left], [right]) => left.localeCompare(right)));
    }

    if (stored.state === "unknown" && stored.payload && !stored.fingerprint) {
      try {
        stored.fingerprint = JSON.stringify(JSON.parse(stored.payload)
          .filter(([name]) => name !== "confirm_date")
          .sort(([left], [right]) => left.localeCompare(right)));
      } catch (_error) { /* an invalid old snapshot will require an explicit new request */ }
    }

    function showPublishRecovery(message, blocking = true) {
      status.hidden = false;
      status.textContent = message;
      recoveryActions.hidden = false;
      restoreButton.disabled = stored?.state === "conflict";
      submit.disabled = blocking;
    }

    if (stored.state === "unknown" && stored.payload) {
      restoreFormPayload(form, stored.payload);
      status.hidden = false;
      status.textContent = "The earlier publish result is unknown. Your exact form and request ID were restored; retry unchanged to reconcile safely.";
      updateCreatePreview();
    } else if (stored.state === "conflict") {
      showPublishRecovery("This request ID conflicts with an earlier request. Refresh, or explicitly start a new request for the current form.");
    }
    writeSessionJson(storageKey, stored);

    form.addEventListener("input", () => {
      if (stored?.state === "unknown" && stored.fingerprint !== fingerprintSnapshot()) {
        showPublishRecovery("The earlier publish may already exist. Restore its exact values to reconcile, or explicitly use these edits as a new request.");
      } else if (["validation", "failed"].includes(stored?.state) && stored.fingerprint !== fingerprintSnapshot()) {
        requestInput.value = createUuid();
        stored = { requestId: requestInput.value, state: "draft" };
        writeSessionJson(storageKey, stored);
        recoveryActions.hidden = true;
        submit.disabled = false;
        status.hidden = false;
        status.textContent = "Your edit starts a fresh request after the known unsuccessful attempt.";
      }
    });

    restoreButton.addEventListener("click", () => {
      if (!stored?.payload || stored.state === "conflict") return;
      restoreFormPayload(form, stored.payload);
      requestInput.value = stored.requestId;
      recoveryActions.hidden = true;
      submit.disabled = false;
      status.hidden = false;
      status.textContent = "Uncertain request restored. Retry now to reconcile with the same request ID.";
      updateCreatePreview();
    });

    newButton.addEventListener("click", () => {
      requestInput.value = createUuid();
      stored = { requestId: requestInput.value, state: "draft" };
      writeSessionJson(storageKey, stored);
      recoveryActions.hidden = true;
      submit.disabled = false;
      status.hidden = false;
      status.textContent = "New request ready. This may create a second card if the uncertain request succeeded.";
    });

    form.addEventListener("submit", async (event) => {
      if (event.submitter && event.submitter.value !== "publish") return;
      event.preventDefault();
      if (stored?.state === "conflict" || (stored?.state === "unknown" && stored.fingerprint !== fingerprintSnapshot())) {
        showPublishRecovery("Resolve the uncertain request before publishing these values.");
        return;
      }
      submit.disabled = true;
      form.setAttribute("aria-busy", "true");
      status.hidden = false;
      status.textContent = "Publishing…";
      const body = new FormData(form);
      body.set("action", "publish");
      body.set("request_id", requestInput.value);
      stored = { requestId: requestInput.value, state: "sending", payload: payloadSnapshot(), fingerprint: fingerprintSnapshot() };
      writeSessionJson(storageKey, stored);
      try {
        const response = await fetchWithTimeout(form.getAttribute("action") || window.location.href, {
          method: "POST",
          body,
          credentials: "same-origin",
          headers: { Accept: "text/html, application/json" },
        }, 15000);
        if (response.redirected) {
          writeSessionJson(storageKey, null);
          window.location.assign(response.url);
          return;
        }
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("text/html")) {
          const html = await response.text();
          stored = { ...stored, state: response.status === 409 ? "conflict" : "validation" };
          writeSessionJson(storageKey, stored);
          document.open();
          document.write(html);
          document.close();
          return;
        }
        let data = {};
        try { data = await response.json(); } catch (_error) { /* use fallback */ }
        stored = { ...stored, state: response.status === 409 ? "conflict" : "failed" };
        writeSessionJson(storageKey, stored);
        status.textContent = data.error || "The card was not published. Review the form and retry with the same request.";
        if (response.status === 409) showPublishRecovery(status.textContent);
      } catch (_error) {
        stored = { ...stored, state: "unknown" };
        writeSessionJson(storageKey, stored);
        status.textContent = "Connection lost. The result is unknown; retrying will use the same request safely.";
      } finally {
        submit.disabled = stored?.state === "conflict" || (stored?.state === "unknown" && stored.fingerprint !== fingerprintSnapshot());
        form.removeAttribute("aria-busy");
      }
    });
  }

  function showChatWarning(warning, message) {
    if (!warning) return;
    warning.textContent = message;
    warning.hidden = !message;
  }

  function pendingBubble(list) {
    return list.querySelector("[data-pending-message]");
  }

  function renderPendingMessage(list, draft) {
    let bubble = pendingBubble(list);
    if (!bubble) {
      list.querySelector("[data-empty-chat]")?.remove();
      bubble = document.createElement("div");
      bubble.className = "bubble mine pending";
      bubble.dataset.pendingMessage = "true";
      bubble.append(document.createElement("small"), document.createElement("p"));
      list.append(bubble);
    }
    const labels = { sending: "You · sending…", fail: "You · failed to send", unknown: "You · result unknown", draft: "You · not sent" };
    bubble.querySelector("small").textContent = labels[draft.state] || labels.unknown;
    bubble.querySelector("p").textContent = draft.text;
    bubble.dataset.sendState = draft.state;
    list.scrollTop = list.scrollHeight;
  }

  function phaseLabel(phase) {
    return ({ waiting: "Waiting", chatting: "Chatting", agreed: "Agreed", declined: "Declined", expired: "Expired" })[phase] || phase;
  }

  function syncChatSendControls(root, isSending = false) {
    root.dataset.chatSendBusy = String(isSending);
    const canChat = root.dataset.chatStatus === "chatting" && root.dataset.chatActive !== "false";
    root.querySelectorAll("[data-chat-action='agree']").forEach((control) => {
      control.disabled = !canChat || root.dataset.planCanConfirm === "false"
        || root.dataset.planRequestPending === "true" || root.dataset.planEditingDirty === "true";
    });
    root.querySelectorAll("[data-chat-action='ai'], [data-quick-reply], [data-opener-suggestion]")
      .forEach((control) => { control.disabled = !canChat || isSending; });
    const input = root.querySelector("[data-chat-form] input[name='message']");
    const submit = root.querySelector("[data-chat-form] button[type='submit']");
    if (input) {
      input.disabled = !canChat;
      input.readOnly = isSending;
    }
    if (submit) submit.disabled = !canChat || isSending;
  }

  function applyChatPhase(root, payload, isSending = false) {
    const phase = payload.phase || payload.chat_status || root.dataset.chatStatus;
    const previous = root.dataset.chatStatus;
    const open = phase === "waiting" || phase === "chatting";
    root.dataset.chatStatus = phase;
    if (payload.chat_active !== undefined) root.dataset.chatActive = String(Boolean(payload.chat_active));
    else if (!root.dataset.chatActive) root.dataset.chatActive = String(phase === "chatting");
    root.className = root.className.replace(/\bphase-\S+/g, "").trim();
    root.classList.add(`phase-${phase}`);

    const label = root.querySelector("[data-phase-label]");
    if (label) label.textContent = phaseLabel(phase);
    const deadline = payload.phase_deadline;
    if (deadline) {
      root.dataset.phaseDeadline = deadline;
      const countdown = root.querySelector("[data-phase-countdown]");
      if (countdown) countdown.dataset.deadline = deadline;
    }
    if (payload.server_time) setServerClock(payload.server_time);

    const viewerAgreed = Boolean(payload.plan?.viewer_confirmed ?? payload.viewer_agreed);
    const otherAgreed = Boolean(payload.plan?.other_confirmed ?? payload.other_agreed);
    if (payload.viewer_agreed !== undefined || payload.plan) root.querySelector("[data-viewer-agreed]").textContent = viewerAgreed ? "yes" : "no";
    if (payload.other_agreed !== undefined || payload.plan) root.querySelector("[data-other-agreed]").textContent = otherAgreed ? "yes" : "no";
    if (payload.viewer_present !== undefined) root.querySelector("[data-viewer-present]").textContent = payload.viewer_present ? "recently seen" : "not recently seen";
    if (payload.other_present !== undefined) root.querySelector("[data-other-present]").textContent = payload.other_present ? "recently seen" : "not recently seen";
    root.querySelector("[data-open-phase-controls]").hidden = !open;
    const compose = root.querySelector("[data-chat-compose]");
    if (compose) compose.hidden = phase !== "chatting";
    root.querySelector("[data-chat-conversation]").hidden = phase === "waiting";
    root.querySelector("[data-shared-plan]").hidden = phase !== "chatting";
    root.querySelector("[data-agree-state]").hidden = phase !== "chatting";
    const timerLabel = root.querySelector("[data-timer-label]");
    if (timerLabel) timerLabel.textContent = phase === "waiting" ? "Time to join" : "Time to decide";
    const timer = root.querySelector("[data-phase-timer]");
    if (timer) timer.hidden = !open;
    root.querySelector("[data-waiting-notice]").hidden = phase !== "waiting";
    root.querySelector("[data-handoff-card]").hidden = phase !== "agreed";
    const closedNotice = root.querySelector("[data-closed-notice]");
    closedNotice.hidden = open;
    if (!open) {
      closedNotice.textContent = phase === "agreed" ? "Chat complete. Use the meeting plan to coordinate your arrival."
        : phase === "declined" ? "A participant exited this match."
          : (previous === "waiting" ? "The wait ended before you both joined. No chat started." : "Time ran out before you both agreed. No meetup was confirmed.");
    }
    root.querySelector("[data-agreement-controls]").hidden = phase !== "chatting" || viewerAgreed;
    root.querySelector("[data-viewer-agreed-notice]").hidden = phase !== "chatting" || !viewerAgreed;
    root.querySelector("[data-other-agreed-notice]").hidden = phase !== "chatting" || !otherAgreed || viewerAgreed;
    syncChatSendControls(root, isSending);

    const guidance = root.querySelector("[data-phase-guidance]");
    if (guidance) {
      guidance.textContent = phase === "waiting" ? "Both people need to open this page before the five-minute chat starts."
        : phase === "chatting" ? "Check the details, then both agree to the same meeting plan."
          : phase === "agreed" ? "Your chat is complete. Keep your meeting plan here." : "This match is closed. Your history and safety report remain available.";
    }
    const meetingLabel = root.querySelector("[data-plan-time-label]");
    if (meetingLabel) meetingLabel.textContent = phase === "agreed" ? "Meeting time" : "Planned meeting";
    if (payload.plan) root.dispatchEvent(new CustomEvent("plusone:planupdate", { detail: payload.plan }));
    if (previous && previous !== phase) {
      const update = root.querySelector("[data-phase-update]");
      update.textContent = phase === "chatting" ? "Your match is here. The five-minute chat has started." : `Match status changed to ${phaseLabel(phase)}.`;
      update.hidden = phase === "agreed";
      setTimeout(() => { if (root.dataset.chatStatus === phase) update.hidden = true; }, 8000);
      root.dispatchEvent(new CustomEvent("plusone:phasechange", { detail: { phase } }));
    }
    updateCountdowns();
    return phase;
  }

  const PLAN_EDIT_FIELDS = ["meeting_point", "meeting_at", "expected_end_at"];

  function createPlanRequest(entries, requestId = createUuid()) {
    return {
      requestId,
      entries: Array.from(entries).filter(([name]) => !["csrfmiddlewaretoken", "request_id"].includes(name))
        .map(([name, value]) => [String(name), String(value)]),
      state: "sending",
    };
  }

  function planEditFingerprint(entries) {
    return JSON.stringify(Array.from(entries).filter(([name]) => PLAN_EDIT_FIELDS.includes(name))
      .sort(([left], [right]) => left.localeCompare(right)));
  }

  function planActionAllowed(plan, action) {
    const permission = {
      update_plan: "can_edit", confirm_plan: "can_confirm", arrived: "can_arrive",
      delayed: "can_delay", cancel_meetup: "can_cancel", outcome: "can_outcome", reopen_card: "can_reopen_card",
    }[action];
    return Boolean(permission && plan?.[permission]);
  }

  function meetupParticipantLabel(status, delayMinutes, outcome) {
    if (outcome === "met") return "Reported: we met";
    if (outcome === "not_met") return "Reported: we didn't meet";
    if (status === "arrived") return "Marked arrived";
    if (status === "delayed") return `Expecting to be ${numericId(delayMinutes)} min late`;
    return "Not marked arrived";
  }

  function meetupPlanPresentation(plan) {
    if (plan?.meetup_status === "cancelled") return {
      title: "Meetup cancelled.", phaseLabel: "Meetup cancelled",
      guidance: "This meetup was cancelled. Nobody should travel to meet. Your agreed plan and report history remain available.",
    };
    if (plan?.meetup_status === "finished") return {
      title: "Meeting window ended.", phaseLabel: "Meeting window ended",
      guidance: "The meeting window has ended. Your plan is saved here; arrival signals are not proof that you met.",
    };
    return {
      title: "Meet handoff ready.", phaseLabel: null,
      guidance: "Both people agreed to this place and time. Find this plan again in My Plus Ones, in this browser.",
    };
  }

  function setupMeetingPlan() {
    const root = document.querySelector("[data-chat-root]");
    if (!root?.dataset.planEndpoint) return;
    const editForm = root.querySelector("[data-plan-edit-form]");
    const recovery = root.querySelector("[data-plan-recovery]");
    const status = root.querySelector("[data-plan-request-status]");
    const retry = root.querySelector("[data-retry-plan-request]");
    const editWarning = root.querySelector("[data-plan-edit-warning]");
    const revisionActions = root.querySelector("[data-plan-revision-actions]");
    const requestKey = `plusone:plan-request:${root.dataset.chatId}`;
    const draftKey = `plusone:plan-edits:${root.dataset.chatId}`;
    let currentPlan = null;
    let editingDirty = false;
    let sending = false;
    let statusTimer = null;
    let pending = readSessionJson(requestKey);
    const savedEdits = readSessionJson(draftKey);
    if (pending?.state === "sending") pending.state = "unknown";

    function textAll(selector, value, fallback = "Not specified") {
      root.querySelectorAll(selector).forEach((node) => { node.textContent = value || fallback; });
    }

    function setRequestStatus(message, canRetry = false, kind = "notice") {
      if (statusTimer !== null) clearTimeout(statusTimer);
      statusTimer = null;
      recovery.hidden = !message;
      recovery.dataset.statusKind = kind;
      status.textContent = message;
      retry.hidden = !canRetry;
      retry.disabled = sending;
      if (kind === "success") statusTimer = setTimeout(() => { recovery.hidden = true; }, 7000);
    }

    function currentEditEntries() {
      return Array.from(new FormData(editForm).entries())
        .filter(([name]) => !["csrfmiddlewaretoken", "request_id"].includes(name));
    }

    function showEditWarning(message, changed = false) {
      editWarning.hidden = !message;
      editWarning.textContent = message;
      revisionActions.hidden = !changed;
    }

    function syncActions() {
      root.dataset.planRequestPending = String(Boolean(sending || pending));
      root.dataset.planEditingDirty = String(editingDirty);
      root.dataset.planCanConfirm = String(Boolean(currentPlan?.can_confirm));
      root.querySelectorAll("[data-plan-action]").forEach((button) => {
        const action = button.dataset.planAction;
        button.disabled = sending || Boolean(pending) || !planActionAllowed(currentPlan, action)
          || (action === "confirm_plan" && editingDirty)
          || (["update_plan", "confirm_plan"].includes(action) && root.dataset.chatActive === "false");
      });
      syncChatSendControls(root, root.dataset.chatSendBusy === "true");
      retry.disabled = sending;
    }

    function restoreLatestInputs() {
      if (!currentPlan) return;
      editForm.elements.meeting_point.value = currentPlan.meeting_point || "";
      editForm.elements.meeting_at.value = currentPlan.meeting_at_input || "";
      editForm.elements.meeting_at.min = currentPlan.earliest_meeting_at_input || "";
      editForm.elements.meeting_at.max = currentPlan.latest_end_at_input || "";
      editForm.elements.expected_end_at.value = currentPlan.expected_end_at_input || "";
      editForm.elements.expected_end_at.min = currentPlan.meeting_at_input || "";
      editForm.elements.expected_end_at.max = currentPlan.latest_end_at_input || "";
      editForm.elements.revision.value = String(currentPlan.revision);
      editingDirty = false;
      writeSessionJson(draftKey, null);
      showEditWarning("");
      syncActions();
    }

    function applyPlan(plan) {
      if (!plan || (currentPlan && numericId(plan.revision) < numericId(currentPlan.revision))) return;
      const previousRevision = currentPlan?.revision;
      currentPlan = plan;
      root.dataset.planRevision = String(plan.revision);
      root.dataset.meetupStatus = plan.meetup_status;
      const windowNotice = root.querySelector("[data-plan-window-notice]");
      if (windowNotice) windowNotice.hidden = root.dataset.chatStatus !== "chatting" || !plan.window_finished;
      editForm.elements.meeting_at.min = plan.earliest_meeting_at_input || "";
      editForm.elements.meeting_at.max = plan.latest_end_at_input || "";
      editForm.elements.expected_end_at.max = plan.latest_end_at_input || "";
      textAll("[data-plan-point]", plan.meeting_point);
      textAll("[data-plan-time]", plan.meeting_at_display);
      textAll("[data-plan-end]", plan.expected_end_at_display, "Not recorded");
      root.querySelectorAll("[data-meetup-form]").forEach((form) => {
        if (form !== editForm || !editingDirty) form.elements.revision.value = String(plan.revision);
      });
      if (!editingDirty) restoreLatestInputs();
      else if (previousRevision !== undefined && String(plan.revision) !== String(editForm.elements.revision.value)) {
        showEditWarning("The shared plan changed. Your edits are kept; review the latest place and time above before using them.", true);
      }
      textAll("[data-viewer-meetup-status]", meetupParticipantLabel(plan.viewer_status, plan.viewer_delay_minutes, plan.viewer_outcome));
      textAll("[data-other-meetup-status]", meetupParticipantLabel(plan.other_status, plan.other_delay_minutes, plan.other_outcome));
      const handoff = root.querySelector("[data-handoff-card]");
      const presentation = meetupPlanPresentation(plan);
      handoff.querySelector("h2").textContent = presentation.title;
      const guidance = root.querySelector("[data-meetup-guidance]");
      guidance.textContent = presentation.guidance;
      if (presentation.phaseLabel) {
        root.querySelector("[data-phase-label]").textContent = presentation.phaseLabel;
        root.querySelector("[data-phase-guidance]").textContent = presentation.guidance;
      }
      root.querySelector("[data-clear-delay]").hidden = plan.viewer_status !== "delayed";
      root.querySelector("[data-meetup-logistics]").hidden = ["cancelled", "finished"].includes(plan.meetup_status);
      root.querySelector("[data-reopen-card]").hidden = !plan.can_reopen_card;
      const recorded = root.querySelector("[data-outcome-recorded]");
      recorded.hidden = !plan.viewer_outcome;
      recorded.textContent = plan.viewer_outcome === "met" ? "You reported that you met. Thanks for the feedback."
        : "You reported that you didn't meet. Your feedback is recorded.";
      root.querySelector("[data-outcome-actions]").hidden = Boolean(plan.viewer_outcome);
      syncActions();
    }

    root.addEventListener("plusone:planupdate", (event) => applyPlan(event.detail));
    let initial = null;
    try { initial = JSON.parse(document.getElementById("meetup-initial-payload")?.textContent || "null"); } catch (_error) { /* polling supplies a fresh state */ }
    if (initial?.plan) applyChatPhase(root, initial);

    if (savedEdits?.entries || pending?.entries?.some(([name]) => PLAN_EDIT_FIELDS.includes(name))) {
      const entries = savedEdits?.entries || pending.entries;
      restoreFormPayload(editForm, JSON.stringify(entries.filter(([name]) => !["csrfmiddlewaretoken", "request_id"].includes(name))));
      editingDirty = true;
      root.querySelector("[data-shared-plan]").open = true;
      const changed = currentPlan && String(currentPlan.revision) !== String(editForm.elements.revision.value);
      showEditWarning(changed ? "Your unfinished edits were restored, but the shared plan changed. Review it before proposing your edits."
        : "Your unfinished plan edits were restored. Save them before agreeing.", Boolean(changed));
    }
    if (pending?.entries && pending.requestId) {
      writeSessionJson(requestKey, pending);
      setRequestStatus("The earlier action's response was not received. Its details are kept; check it again before taking another action.", true);
    } else pending = null;
    root.querySelectorAll("[data-meetup-form]").forEach((form) => { form.elements.request_id.value = createUuid(); });
    syncActions();

    function onPlanEdit() {
      editingDirty = true;
      editForm.elements.expected_end_at.min = editForm.elements.meeting_at.value;
      writeSessionJson(draftKey, { entries: currentEditEntries() });
      const changed = currentPlan && String(currentPlan.revision) !== String(editForm.elements.revision.value);
      showEditWarning(changed ? "The shared plan changed. Your edits are kept. Review the latest details before proposing them."
        : "You have unsaved plan changes. Save them before agreeing.", Boolean(changed));
      syncActions();
    }
    editForm.addEventListener("input", onPlanEdit);
    editForm.addEventListener("change", onPlanEdit);
    root.querySelector("[data-use-latest-plan]").addEventListener("click", restoreLatestInputs);
    root.querySelector("[data-keep-plan-edits]").addEventListener("click", () => {
      if (!currentPlan) return;
      editForm.elements.revision.value = String(currentPlan.revision);
      writeSessionJson(draftKey, { entries: currentEditEntries() });
      showEditWarning("Your edits will replace the latest shared plan and ask both people to agree again.");
      syncActions();
    });

    function mutationBoundary() {
      root.dataset.planMutationGeneration = String(numericId(root.dataset.planMutationGeneration) + 1);
    }

    async function sendAction(request) {
      if (sending) return;
      sending = true;
      pending = { ...request, state: "sending" };
      writeSessionJson(requestKey, pending);
      mutationBoundary();
      syncActions();
      setRequestStatus("Saving your action…");
      const body = new FormData();
      request.entries.forEach(([name, value]) => body.append(name, value));
      body.set("request_id", request.requestId);
      body.set("csrfmiddlewaretoken", root.querySelector("[name=csrfmiddlewaretoken]").value);
      const action = body.get("action");
      try {
        const response = await fetchWithTimeout(root.dataset.planEndpoint, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } }, 12000);
        let data = null;
        try { data = await response.json(); } catch (_error) { /* a missing response cannot establish failure */ }
        if (!data || response.status >= 500) throw new Error("unconfirmed response");
        mutationBoundary();
        if (response.ok) {
          pending = null;
          writeSessionJson(requestKey, null);
          if (action === "update_plan" && planEditFingerprint(currentEditEntries()) === planEditFingerprint(request.entries)) {
            editingDirty = false;
            writeSessionJson(draftKey, null);
            showEditWarning("");
          }
          if (data.plan) applyChatPhase(root, data);
          const messages = {
            update_plan: "Meeting plan saved. Both people need to agree to this version.",
            confirm_plan: "Your agreement to this plan is recorded.",
            arrived: "Your arrival is recorded. Your Plus One can see it here.",
            delayed: "Your delay update is recorded. The agreed meeting time stays the same.",
            cancel_meetup: "Meetup cancelled. Your Plus One can see this on the plan.",
            outcome: "Your meetup feedback is recorded.",
            reopen_card: "Your original card is open for a new Plus One. This cancelled meetup remains in history.",
          };
          const submittedRevision = new Map(request.entries).get("revision");
          const successMessage = data.replayed ? "Your earlier action was received. The latest plan and status are shown here."
            : action === "update_plan" && String(data.action_result?.revision) === String(submittedRevision)
              ? "The meeting plan is up to date. Existing agreements have been kept."
              : messages[action] || "Your action is recorded.";
          setRequestStatus(successMessage, false, "success");
        } else {
          pending = null;
          writeSessionJson(requestKey, null);
          if (data.plan) applyChatPhase(root, data);
          const message = data.error || "This action was not saved. Review the current plan and try again.";
          setRequestStatus(response.status === 409 ? `${message} Review the latest plan before choosing again.` : message);
          if (action === "update_plan" && response.status === 409 && editingDirty) showEditWarning("The shared plan changed. Your edits are kept; review the latest details before proposing them.", true);
        }
      } catch (_error) {
        pending = { ...request, state: "unknown" };
        writeSessionJson(requestKey, pending);
        setRequestStatus("Connection interrupted. This action may already be saved. Check the same action again; its original details are kept.", true);
      } finally {
        sending = false;
        syncActions();
      }
    }

    root.querySelectorAll("[data-meetup-form]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (sending || pending || !currentPlan) return;
        const action = form.elements.action.value;
        if (!planActionAllowed(currentPlan, action) || (action === "confirm_plan" && editingDirty)) return;
        if (form.dataset.meetupConfirm && !window.confirm(form.dataset.meetupConfirm)) return;
        const body = new FormData(form);
        if (event.submitter?.name) body.set(event.submitter.name, event.submitter.value);
        const request = createPlanRequest(body.entries(), createUuid());
        form.elements.request_id.value = request.requestId;
        sendAction(request);
      });
    });
    retry.addEventListener("click", () => { if (pending && !sending) sendAction(pending); });
  }

  function setupChat() {
    const root = document.querySelector("[data-chat-root]");
    const list = root?.querySelector("[data-chat-messages]");
    if (!root || !list) return;
    setServerClock(root.dataset.serverTime);
    let cursor = numericId(list.dataset.lastMessageId);
    let pollLoop;
    let isSending = false;
    let draftConflict = false;

    const form = root.querySelector("[data-chat-form]");
    const input = form?.querySelector("input[name='message']");
    const requestInput = form?.querySelector("[data-request-id]");
    const submit = form?.querySelector("button[type='submit']");
    const warning = form?.querySelector("[data-chat-warning]");
    const recoveryActions = form?.querySelector("[data-chat-recovery-actions]");
    const restoreButton = form?.querySelector("[data-restore-chat-request]");
    const newButton = form?.querySelector("[data-new-chat-request]");
    const recovery = root.querySelector("[data-chat-draft-recovery]");
    const recoveryText = recovery.querySelector("[data-chat-recovery-text]");
    const recoveryRetry = recovery.querySelector("[data-retry-chat-request]");
    const recoveryWarning = recovery.querySelector("[data-chat-recovery-warning]");
    const syncStatus = root.querySelector("[data-chat-sync-status]");
    const storageKey = `plusone:chat-draft:${root.dataset.chatId}`;
    let savedDraft = readSessionJson(storageKey);

    function showDraftRecovery(draft, message = "") {
      if (!draft?.text) return;
      recovery.hidden = false;
      recoveryText.value = draft.text;
      recoveryRetry.hidden = !["sending", "unknown", "fail", "conflict"].includes(draft.state);
      recoveryWarning.hidden = !message;
      recoveryWarning.textContent = message;
    }

    function clearConfirmedDraft(draft) {
      pendingBubble(list)?.remove();
      if (input?.value === draft?.text) input.value = "";
      if (requestInput) requestInput.value = createUuid();
      savedDraft = null;
      draftConflict = false;
      writeSessionJson(storageKey, null);
      recovery.hidden = true;
      if (recoveryActions) recoveryActions.hidden = true;
      showChatWarning(warning, "");
    }

    function showDraftConflict(message) {
      draftConflict = true;
      if (recoveryActions) recoveryActions.hidden = false;
      showChatWarning(warning, message);
      if (submit) submit.disabled = true;
    }

    const initiallyConfirmed = savedDraft?.requestId && hasRenderedRequestId(list, savedDraft.requestId);
    if (initiallyConfirmed) clearConfirmedDraft(savedDraft);

    if (savedDraft?.text) {
      savedDraft.state = savedDraft.state === "sending" ? "unknown" : savedDraft.state;
      writeSessionJson(storageKey, savedDraft);
      if (input && requestInput) {
        input.value = savedDraft.text;
        requestInput.value = savedDraft.requestId;
      }
      renderPendingMessage(list, savedDraft);
      showDraftRecovery(savedDraft, savedDraft.state === "unknown" ? "The earlier result is unknown. Retry the same request to reconcile it safely." : "");
      if (savedDraft.state === "conflict" && form) showDraftConflict("This request ID conflicts with an earlier message. Restore or explicitly start a new message request.");
    } else if (requestInput) {
      requestInput.value = requestInput.value || createUuid();
    }

    async function fetchMessages() {
      const planGeneration = root.dataset.planMutationGeneration;
      try {
        const response = await fetchWithTimeout(`${root.dataset.chatEndpoint}?after=${encodeURIComponent(cursor)}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }, 8000);
        if (!response.ok) throw new Error(`poll ${response.status}`);
        const data = await response.json();
        cursor = applyPollPayload(list, data, cursor);
        list.dataset.lastMessageId = String(cursor);
        const recovered = savedDraft && findRecoveredMessage(data.messages, savedDraft.requestId);
        if (recovered) {
          clearConfirmedDraft(savedDraft);
        }
        if (syncStatus) {
          syncStatus.textContent = "";
          syncStatus.hidden = true;
        }
        // An older poll must not undo a plan action whose result arrived later.
        if (planGeneration !== root.dataset.planMutationGeneration) return;
        const phase = applyChatPhase(root, data, isSending || draftConflict);
        if (phase !== "waiting" && phase !== "chatting") {
          if (phase !== "agreed") pollLoop.stop();
          if (savedDraft) showDraftRecovery(savedDraft, "The chat ended, but this unconfirmed text remains available to copy or reconcile.");
        }
      } catch (error) {
        if (syncStatus) {
          syncStatus.textContent = "Connection interrupted. Retrying; messages and meeting updates may be out of date.";
          syncStatus.hidden = false;
        }
        root.querySelectorAll("[data-viewer-present], [data-other-present]").forEach((node) => { node.textContent = "unable to check"; });
        throw error;
      }
    }

    pollLoop = createPollLoop({
      task: fetchMessages,
      isVisible: () => document.visibilityState !== "hidden",
      baseDelay: 2000,
      maxDelay: 16000,
    });
    if (["waiting", "chatting", "agreed"].includes(root.dataset.chatStatus)) pollLoop.start();
    root.addEventListener("plusone:phasechange", (event) => {
      if (["waiting", "chatting", "agreed"].includes(event.detail.phase)) pollLoop.start();
      else pollLoop.stop();
    });

    async function sendDraft(draft, allowClosedReplay = false) {
      if (isSending || !draft?.text || !draft.requestId) return;
      if (!allowClosedReplay && root.dataset.chatStatus !== "chatting") return;
      isSending = true;
      draft.state = "sending";
      savedDraft = draft;
      writeSessionJson(storageKey, draft);
      renderPendingMessage(list, draft);
      showDraftRecovery(draft, "Checking this request…");
      showChatWarning(warning, "");
      syncChatSendControls(root, true);

      const body = new FormData();
      const csrf = root.querySelector("[name=csrfmiddlewaretoken]")?.value;
      if (csrf) body.set("csrfmiddlewaretoken", csrf);
      body.set("message", draft.text);
      body.set("request_id", draft.requestId);
      try {
        const response = await fetchWithTimeout(root.dataset.chatEndpoint, {
          method: "POST",
          body,
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }, 12000);
        let data = {};
        try { data = await response.json(); } catch (_error) { /* handled below */ }
        if (response.ok && data.message) {
          appendChatMessage(list, data.message); // POST rendering never advances the GET cursor.
          clearConfirmedDraft(draft);
          pollLoop.trigger();
        } else if (hasRenderedRequestId(list, draft.requestId)) {
          clearConfirmedDraft(draft);
        } else {
          const isConflict = response.status === 409 && /request id|different content|already used/i.test(data.error || "");
          draft.state = response.ok ? "unknown" : (isConflict ? "conflict" : "fail");
          savedDraft = draft;
          writeSessionJson(storageKey, draft);
          renderPendingMessage(list, draft);
          const message = data.warning || data.error || (response.ok ? "The result is unknown. Retry the same request safely." : "Message was not sent.");
          showChatWarning(warning, message);
          showDraftRecovery(draft, message);
          if (isConflict) showDraftConflict(message);
        }
      } catch (_error) {
        if (hasRenderedRequestId(list, draft.requestId)) {
          clearConfirmedDraft(draft);
        } else {
          draft.state = "unknown";
          savedDraft = draft;
          writeSessionJson(storageKey, draft);
          renderPendingMessage(list, draft);
          const message = "Connection timed out or was lost. The result is unknown; retry the same request safely.";
          showChatWarning(warning, message);
          showDraftRecovery(draft, message);
        }
      } finally {
        isSending = false;
        syncChatSendControls(root, draftConflict);
        input?.focus();
      }
    }

    if (form && input && requestInput) {
      input.addEventListener("input", () => {
        const text = input.value;
        if (savedDraft && ["unknown", "conflict"].includes(savedDraft.state) && text !== savedDraft.text) {
          showDraftConflict("The earlier request is unresolved. Restore it, or explicitly use this edit as a new message.");
          return;
        }
        if (savedDraft?.state === "fail" && text !== savedDraft.text) {
          requestInput.value = createUuid();
          savedDraft = text ? { requestId: requestInput.value, text, state: "draft" } : null;
          writeSessionJson(storageKey, savedDraft);
          pendingBubble(list)?.remove();
          recovery.hidden = true;
          if (recoveryActions) recoveryActions.hidden = true;
          showChatWarning(warning, "Your edit starts a fresh request after the known failed send.");
          return;
        }
        savedDraft = text ? { requestId: requestInput.value, text, state: "draft" } : null;
        writeSessionJson(storageKey, savedDraft);
      });

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (isSending || draftConflict) return;
        const text = input.value.trim();
        if (!text || root.dataset.chatStatus !== "chatting") return;
        const draft = { requestId: requestInput.value || createUuid(), text, state: "sending" };
        requestInput.value = draft.requestId;
        await sendDraft(draft);
      });

      restoreButton.addEventListener("click", () => {
        if (!savedDraft) return;
        input.value = savedDraft.text;
        requestInput.value = savedDraft.requestId;
        draftConflict = false;
        recoveryActions.hidden = true;
        showChatWarning(warning, "Uncertain message restored. Retry to reconcile the same request.");
        syncChatSendControls(root, false);
      });

      newButton.addEventListener("click", () => {
        requestInput.value = createUuid();
        savedDraft = input.value ? { requestId: requestInput.value, text: input.value, state: "draft" } : null;
        writeSessionJson(storageKey, savedDraft);
        draftConflict = false;
        recoveryActions.hidden = true;
        pendingBubble(list)?.remove();
        recovery.hidden = true;
        showChatWarning(warning, "New message request ready. The earlier uncertain send may still appear if it succeeded.");
        syncChatSendControls(root, false);
      });
    }

    recoveryRetry.addEventListener("click", () => {
      if (savedDraft && !isSending) sendDraft({ ...savedDraft }, true);
    });

    root.querySelectorAll("[data-quick-reply]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!input || button.disabled) return;
        input.value = button.dataset.quickReply || "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
        showChatWarning(warning, "");
        trackOpenerClick(button);
      });
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && ["waiting", "chatting", "agreed"].includes(root.dataset.chatStatus)) pollLoop.start();
      else pollLoop.stop();
    });
  }

  function trackOpenerClick(button) {
    const container = button.closest("[data-opener-track-url]");
    if (!container || button.dataset.openerIndex === undefined) return;
    const csrf = document.querySelector("[name=csrfmiddlewaretoken]");
    if (!csrf) return;
    const body = new FormData();
    body.append("index", button.dataset.openerIndex);
    body.append("csrfmiddlewaretoken", csrf.value);
    fetch(container.dataset.openerTrackUrl, { method: "POST", body, credentials: "same-origin" }).catch(() => {});
  }

  function setupPresence() {
    const root = document.querySelector("[data-chat-root]");
    if (!root?.dataset.presenceEndpoint) return;
    const csrf = root.querySelector("[name=csrfmiddlewaretoken]")?.value;
    if (!csrf) return;

    function presenceBody(visible) {
      const body = new FormData();
      body.set("visible", visible ? "true" : "false");
      body.set("csrfmiddlewaretoken", csrf);
      return body;
    }

    async function postVisible() {
      if (root.dataset.chatStatus !== "waiting" && root.dataset.chatStatus !== "chatting") return;
      const response = await fetchWithTimeout(root.dataset.presenceEndpoint, {
        method: "POST",
        body: presenceBody(true),
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      }, 4000);
      if (!response.ok) throw new Error(`presence ${response.status}`);
    }

    function postHidden() {
      if (root.dataset.chatStatus !== "waiting" && root.dataset.chatStatus !== "chatting") return;
      const body = presenceBody(false);
      if (navigator.sendBeacon?.(root.dataset.presenceEndpoint, body)) return;
      fetch(root.dataset.presenceEndpoint, { method: "POST", body, credentials: "same-origin", keepalive: true }).catch(() => {});
    }

    const loop = createPollLoop({ task: postVisible, isVisible: () => document.visibilityState !== "hidden", baseDelay: 5000, maxDelay: 5000 });
    if (root.dataset.chatStatus === "waiting" || root.dataset.chatStatus === "chatting") loop.start();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && (root.dataset.chatStatus === "waiting" || root.dataset.chatStatus === "chatting")) loop.start();
      else {
        loop.stop();
        postHidden();
      }
    });
    root.addEventListener("plusone:phasechange", (event) => {
      if (event.detail.phase !== "waiting" && event.detail.phase !== "chatting") loop.stop();
    });
    window.addEventListener("pagehide", postHidden);
  }

  function showToast(message, url) {
    const toast = document.querySelector("[data-live-toast]");
    if (!toast) return;
    toast.replaceChildren();
    const text = document.createElement(url ? "a" : "span");
    text.textContent = message;
    if (url) text.href = url;
    toast.append(text);
    toast.hidden = false;
    setTimeout(() => { toast.hidden = true; }, 12000);
  }

  function setupSessionPolling() {
    const endpoint = document.body.dataset.sessionUpdatesUrl;
    if (!endpoint) return;
    let initialized = false;
    let previous = new Map();
    let loop;

    async function fetchSession() {
      const response = await fetchWithTimeout(endpoint, { credentials: "same-origin", headers: { Accept: "application/json" } }, 4000);
      if (!response.ok) throw new Error(`session ${response.status}`);
      const data = await response.json();
      if (!data.authenticated) {
        loop.stop();
        return;
      }
      setServerClock(data.server_time);
      const matches = Array.isArray(data.matches) ? data.matches : [];
      const current = new Map(matches.map((match) => [String(match.id), match.status]));
      if (initialized) {
        const changed = matches.find((match) => !previous.has(String(match.id)) || (match.status === "waiting" && previous.get(String(match.id)) !== "waiting"));
        if (changed) showToast(`${changed.status === "waiting" ? "A match is waiting" : "New match"}: ${changed.title}`, changed.url);
      }
      initialized = true;
      previous = current;
      let badge = document.querySelector(".nav-badge");
      const count = numericId(data.open_count);
      if (!badge && count > 0) {
        badge = document.createElement("span");
        badge.className = "nav-badge";
        document.querySelector("[data-dashboard-link]")?.append(badge);
      }
      if (badge) {
        badge.textContent = String(count);
        badge.hidden = count === 0;
      }
    }

    loop = createPollLoop({ task: fetchSession, isVisible: () => document.visibilityState !== "hidden", baseDelay: 5000, maxDelay: 20000 });
    loop.start();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loop.start();
      else loop.stop();
    });
  }

  function setupSubmitConfirms() {
    document.querySelectorAll("[data-confirm-reset], [data-confirm-submit]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        const message = form.dataset.confirmReset || form.dataset.confirmSubmit;
        if (message && !window.confirm(message)) event.preventDefault();
      });
    });
  }

  function initialize() {
    updateCountdowns();
    setInterval(updateCountdowns, 1000);
    updateCreatePreview();
    setupTimeClarifier();
    setupReliablePublish();
    setupMeetingPlan();
    setupChat();
    setupPresence();
    setupSessionPolling();
    setupSubmitConfirms();
    ["id_title", "id_description", "id_activity_type", "id_location", "id_start_time", "id_expected_end_time", "id_expire_minutes"].forEach((id) => {
      const field = document.getElementById(id);
      field?.addEventListener("input", updateCreatePreview);
      field?.addEventListener("change", updateCreatePreview);
    });
  }

  const testApi = {
    advanceCursor,
    applyPollPayload,
    appendChatMessage,
    createPollLoop,
    fetchWithTimeout,
    findRecoveredMessage,
    hasRenderedRequestId,
    numericId,
    restoreFormPayload,
    createPlanRequest,
    planEditFingerprint,
    planActionAllowed,
    meetupParticipantLabel,
    meetupPlanPresentation,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = testApi;
  if (typeof window !== "undefined") window.PlusOneTest = testApi;
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", initialize);
})();

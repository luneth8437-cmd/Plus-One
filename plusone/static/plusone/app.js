(() => {
  "use strict";

  let serverClockOffsetMs = 0;
  let initialSessionScope = null;
  let sessionScopeInvalidated = false;

  function sessionScopedKey(name, scope = initialSessionScope || "anonymous") {
    return `plusone:${encodeURIComponent(scope)}:${name}`;
  }

  function currentSessionIsValid() {
    return !sessionScopeInvalidated;
  }

  function setupSessionScope() {
    initialSessionScope = document.body.dataset.sessionScope || "anonymous";
    if (initialSessionScope !== "anonymous") {
      document.querySelectorAll("form").forEach((form) => {
        if ((form.method || "get").toLowerCase() !== "post" || form.querySelector("[name=session_scope]")) return;
        const scope = document.createElement("input");
        scope.type = "hidden";
        scope.name = "session_scope";
        scope.value = initialSessionScope;
        form.append(scope);
      });
    }
    const activeKey = "plusone:active-session-scope";
    function invalidateSession(nextScope) {
      if (!nextScope || nextScope === initialSessionScope || sessionScopeInvalidated) return;
      sessionScopeInvalidated = true;
      try {
        const prefix = sessionScopedKey("");
        Object.keys(sessionStorage).filter((key) => key.startsWith(prefix)).forEach((key) => sessionStorage.removeItem(key));
      } catch (_error) { /* ownership checks still block every old write */ }
      // An old document must never replay a write using another anonymous identity.
      document.querySelectorAll("form").forEach((form) => {
        form.dataset.sessionExpired = "true";
        Array.from(form.elements).forEach((control) => { control.disabled = true; });
      });
      const notice = document.createElement("section");
      notice.className = "notice error";
      notice.setAttribute("role", "alert");
      const message = document.createElement("p");
      message.textContent = "Your session changed in another tab. Reload to use your current identity. This page's saved requests will not be submitted.";
      const reload = document.createElement("button");
      reload.type = "button";
      reload.textContent = "Reload current session";
      reload.addEventListener("click", () => window.location.reload());
      notice.append(message, reload);
      (document.querySelector("main") || document.body).prepend(notice);
      document.dispatchEvent(new CustomEvent("plusone:sessionchanged"));
    }
    document.addEventListener("submit", (event) => {
      if (!currentSessionIsValid()) event.preventDefault();
    }, true);
    document.addEventListener("plusone:sessionscope", (event) => invalidateSession(event.detail));
    window.addEventListener("storage", (event) => {
      if (event.key === activeKey) invalidateSession(event.newValue);
    });
    try { localStorage.setItem(activeKey, initialSessionScope); } catch (_error) { /* polling also detects resets */ }
    // Old unscoped records have no trustworthy owner. Never restore them into a new identity.
    try {
      Object.keys(sessionStorage).filter((key) => /^plusone:(create-request|chat-draft:|plan-request:|plan-edits:)/.test(key))
        .forEach((key) => sessionStorage.removeItem(key));
    } catch (_error) { /* storage is optional */ }
  }

  function setServerClock(serverTime) {
    if (!serverTime) return;
    const parsed = new Date(serverTime).getTime();
    if (!Number.isNaN(parsed)) serverClockOffsetMs = parsed - Date.now();
  }

  function matchingDeadlineState(deadline, now = Date.now()) {
    const instant = new Date(deadline).getTime();
    return { valid: Number.isFinite(instant), matchable: Number.isFinite(instant) && instant > now };
  }

  function updateDiscoverAvailability(now = Date.now() + serverClockOffsetMs, container = document) {
    container.querySelectorAll("[data-matchable-card]").forEach((card) => {
      const { matchable } = matchingDeadlineState(card.dataset.matchingDeadline, now);
      card.querySelectorAll("[data-card-interest]").forEach((button) => {
        if (button.dataset.initiallyDisabled === undefined) button.dataset.initiallyDisabled = String(button.disabled);
        button.disabled = !matchable || button.dataset.initiallyDisabled === "true" || !currentSessionIsValid();
      });
      const notice = card.querySelector("[data-card-expiry-notice]");
      if (notice) notice.hidden = matchable;
      card.querySelectorAll("[data-card-live-action]").forEach((link) => {
        if (matchable) { link.removeAttribute("aria-disabled"); link.removeAttribute("tabindex"); }
        else { link.setAttribute("aria-disabled", "true"); link.setAttribute("tabindex", "-1"); }
      });
    });
  }

  function setupDiscoverExpiry() {
    const clock = document.querySelector("[data-discovery-server-time]");
    if (clock) setServerClock(clock.dataset.discoveryServerTime);
    if (!document.querySelector("[data-matchable-card]")) return;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") updateCountdowns();
    });
    window.addEventListener("pageshow", updateCountdowns);
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-card-live-action][aria-disabled='true']")) event.preventDefault();
    });
    const queue = document.querySelector("[data-discover-queue]");
    if (!queue) return;
    const filterQuery = new URLSearchParams(window.location.search);
    Array.from(filterQuery.keys()).filter((key) => !["activity_type", "location", "time_window"].includes(key))
      .forEach((key) => filterQuery.delete(key));
    filterQuery.sort();
    const scrollKey = sessionScopedKey(`discover-scroll:${window.location.pathname}?${filterQuery}`);
    const rememberScroll = () => {
      if (!currentSessionIsValid()) return;
      try { sessionStorage.setItem(scrollKey, String(window.scrollY)); } catch (_error) { /* browsing still works without storage */ }
    };
    window.addEventListener("pagehide", rememberScroll);
    queue.addEventListener("submit", rememberScroll);
    queue.addEventListener("click", rememberScroll);
    try {
      const stored = sessionStorage.getItem(scrollKey);
      if (stored !== null && Number.isFinite(Number(stored)) && Number(stored) >= 0) {
        requestAnimationFrame(() => window.scrollTo(0, Number(stored)));
      }
    } catch (_error) { /* scroll restoration is optional */ }
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
    updateDiscoverAvailability(now);
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
    if (!Array.isArray(entries) || entries.some((entry) => !Array.isArray(entry) || entry.length !== 2)) return false;
    const names = new Set(entries.map(([name]) => name));
    Array.from(form.elements).forEach((control) => {
      if (!control.name || control.name === "csrfmiddlewaretoken" || control.name === "request_id") return;
      if ((control.type === "checkbox" || control.type === "radio") && !names.has(control.name)) control.checked = false;
    });
    entries.forEach(([name, value]) => {
      if (["csrfmiddlewaretoken", "request_id", "session_scope"].includes(name)) return;
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
    if (!locationInput?.value) return "Choose a location";
    const label = locationInput.selectedOptions?.[0]?.textContent?.trim();
    return label && label !== "---------" ? label : "Choose a location";
  }

  function previewActivityLabel(activityInput) {
    if (!activityInput?.value) return "Activity";
    const label = activityInput.selectedOptions?.[0]?.textContent?.trim();
    return label && label !== "---------" ? label : "Activity";
  }

  function previewStartTimeLabel(value) {
    if (!value) return "Start time";
    // datetime-local is already campus wall time. Parsing it in the device timezone
    // can change a daylight-saving gap or date, so format its written parts directly.
    const parts = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!parts) return "Start time";
    const month = new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, 1))
      .toLocaleString(undefined, { month: "short", timeZone: "UTC" });
    return `${month} ${Number(parts[3])}, ${parts[4]}:${parts[5]}`;
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

  function reviewedPayloadEntries(entries) {
    const fields = ["title", "description", "activity_type", "location", "start_time", "expected_end_time", "expire_minutes", "raw_text"];
    return Array.from(entries).filter(([name]) => fields.includes(name))
      .map(([name, value]) => [String(name), String(value)]);
  }

  function proposalEntries(fields) {
    const editable = ["title", "description", "activity_type", "location", "start_time", "expected_end_time", "expire_minutes", "raw_text"];
    return Object.entries(fields || {}).filter(([name]) => editable.includes(name))
      .map(([name, value]) => [name, value === null || value === undefined ? "" : String(value)]);
  }

  function createEntriesFingerprint(entries) {
    return JSON.stringify(reviewedPayloadEntries(entries).sort(([left], [right]) => left.localeCompare(right)));
  }

  function restoreNewerCreateDraft(saved, restoreAllowed, reviewToken, serverRevision) {
    return Boolean(saved?.entries && ((!reviewToken && restoreAllowed) || numericId(saved.revision) > numericId(serverRevision)));
  }

  function clearCreateFields(form) {
    const fields = new Set(["title", "description", "activity_type", "location", "start_time", "expected_end_time", "raw_text", "assist_text"]);
    Array.from(form.elements).forEach((control) => {
      if (fields.has(control.name)) control.value = "";
      else if (control.name === "expire_minutes") control.value = "45";
      else if (control.name === "confirm_date") control.checked = false;
      else if (["reviewed_payload", "proposal_payload"].includes(control.name)) control.value = "{}";
    });
  }

  function setupCreateDraft() {
    const form = document.querySelector("[data-create-publish-form]");
    if (!form) return;
    const assist = document.querySelector("[data-assist-form]");
    const assistText = assist?.querySelector("[name=assist_text], textarea[name=raw_text]");
    const draftKey = sessionScopedKey("create-draft");
    const status = document.querySelector("[data-create-draft-status]");
    const saved = readSessionJson(draftKey);
    const reviewToken = form.dataset.reviewToken || "";
    const revisionInput = form.querySelector("[data-create-draft-revision]");
    const serverRevision = numericId(form.dataset.serverDraftRevision);
    let revision = Math.max(serverRevision, numericId(saved?.revision));
    let decision = saved?.reviewToken === reviewToken ? saved?.decision || "" : "";
    if (!reviewToken && saved?.reviewToken && form.dataset.restoreCreateDraft !== "false"
      && new URL(window.location.href).searchParams.get("draft") !== saved.reviewToken) {
      const url = new URL(window.location.href);
      url.searchParams.set("draft", saved.reviewToken);
      window.location.replace(url.href);
      return;
    }
    if (restoreNewerCreateDraft(saved, form.dataset.restoreCreateDraft !== "false", reviewToken, serverRevision)) {
      restoreFormPayload(form, JSON.stringify(saved.entries));
      if (assist && saved.assistText !== undefined) {
        if (assistText) assistText.value = saved.assistText;
      }
      if (status) {
        status.hidden = false;
        status.textContent = "Your unfinished draft was restored in this tab. Review its time before publishing.";
      }
      updateCreatePreview();
    }
    function saveDraft(increment = true) {
      if (!currentSessionIsValid()) return;
      if (increment) revision += 1;
      if (revisionInput) revisionInput.value = String(revision);
      const draft = {
        entries: reviewedPayloadEntries(new FormData(form).entries()),
        assistText: assistText?.value || "",
        revision, reviewToken, decision,
      };
      let stored = false;
      try { sessionStorage.setItem(draftKey, JSON.stringify(draft)); stored = true; } catch (_error) { /* report the unavailable cache honestly */ }
      if (status) {
        status.hidden = false;
        const message = stored ? "Draft saved in this browser tab." : "This browser could not save your draft. Keep this page open while reviewing.";
        if (status.textContent !== message) status.textContent = message;
      }
    }
    if (revisionInput) revisionInput.value = String(revision);
    form.addEventListener("input", () => saveDraft());
    form.addEventListener("change", () => saveDraft());
    if (assist !== form) assist?.addEventListener("input", () => saveDraft());
    assist?.addEventListener("submit", (event) => {
      if (event.submitter && event.submitter.value !== "assist") return;
      if (!currentSessionIsValid()) return;
      saveDraft(false);
      let reviewed = assist.querySelector("[name=reviewed_payload]");
      if (!reviewed) {
        reviewed = document.createElement("input");
        reviewed.type = "hidden";
        reviewed.name = "reviewed_payload";
        assist.append(reviewed);
      }
      reviewed.value = JSON.stringify(reviewedPayloadEntries(new FormData(form).entries()));
    });
    document.querySelector("[data-clear-create-draft]")?.addEventListener("click", (event) => {
      const request = readSessionJson(sessionScopedKey("create-request"));
      if (["sending", "unknown", "conflict"].includes(request?.state) || form.dataset.publishSending === "true" || form.dataset.publishUncertain === "true") {
        event.preventDefault();
        if (status) status.textContent = "Resolve the uncertain publish request before discarding this draft.";
        return;
      }
      clearCreateFields(form);
      writeSessionJson(draftKey, null);
      writeSessionJson(sessionScopedKey("create-request"), null);
      form.dispatchEvent(new Event("plusone:createclear"));
      if (status) { status.hidden = false; status.textContent = "Draft discarded. Start with new details."; }
      updateCreatePreview();
    });
    let proposal;
    try { proposal = JSON.parse(document.getElementById("activity-draft-proposal")?.textContent || "null"); } catch (_error) { /* a malformed proposal cannot replace a draft */ }
    const proposalSection = document.querySelector("[data-draft-proposal]");
    if (proposalSection && decision) proposalSection.hidden = true;
    if (reviewToken) saveDraft(false);
    document.querySelector("[data-apply-draft-proposal]")?.addEventListener("click", (event) => {
      if (!proposal?.fields || !currentSessionIsValid()) return;
      event.preventDefault();
      restoreFormPayload(form, JSON.stringify(proposalEntries(proposal.fields)));
      decision = "applied";
      form.dispatchEvent(new Event("input", { bubbles: true }));
      form.dispatchEvent(new Event("change", { bubbles: true }));
      if (proposalSection) proposalSection.hidden = true;
      if (status) { status.hidden = false; status.textContent = "Suggested details applied. Review the place and time before publishing."; }
      updateCreatePreview();
    });
    document.querySelector("[data-keep-reviewed-draft]")?.addEventListener("click", (event) => {
      if (!currentSessionIsValid()) return;
      event.preventDefault();
      if (proposalSection) proposalSection.hidden = true;
      decision = "kept";
      saveDraft();
      if (status) { status.hidden = false; status.textContent = "Your reviewed details were kept."; }
    });
  }

  function publishedRequestLeavesDraft(draft, request) {
    if (!draft?.entries) return false;
    return numericId(draft.revision) > numericId(request.draftRevision)
      || createEntriesFingerprint(draft.entries) !== request.fingerprint
      || (draft.assistText || "") !== (request.assistText || "");
  }

  function setupReliablePublish() {
    const form = document.querySelector("[data-create-publish-form]");
    if (!form) return;
    const requestInput = form.querySelector("[data-request-id]");
    const status = form.querySelector("[data-publish-status]");
    const recoveryActions = form.querySelector("[data-publish-recovery-actions]");
    const restoreButton = form.querySelector("[data-restore-publish-request]");
    const newButton = form.querySelector("[data-new-publish-request]");
    const originalDetails = form.querySelector("[data-publish-original-details]");
    const originalFields = form.querySelector("[data-publish-original-fields]");
    const storageKey = sessionScopedKey("create-request");
    const draftKey = sessionScopedKey("create-draft");
    let stored = readSessionJson(storageKey);
    let sending = false;
    if (stored?.state === "sending") stored.state = "unknown";
    requestInput.value = stored?.requestId || requestInput.value || createUuid();
    stored = { ...stored, requestId: requestInput.value, state: stored?.state || "draft" };

    function currentEntries() { return reviewedPayloadEntries(new FormData(form).entries()); }
    function currentFingerprint() { return createEntriesFingerprint(currentEntries()); }
    function payloadEntries(payload) {
      try {
        const entries = JSON.parse(payload || "[]");
        return Array.isArray(entries) && entries.every((entry) => Array.isArray(entry) && entry.length === 2) ? entries : [];
      } catch (_error) { return []; }
    }
    function syncControls() {
      const unresolved = ["unknown", "conflict"].includes(stored?.state);
      form.dataset.publishSending = String(sending);
      form.dataset.publishUncertain = String(unresolved);
      const blocked = !currentSessionIsValid() || sending || stored?.state === "conflict"
        || (stored?.state === "unknown" && stored.fingerprint !== currentFingerprint());
      document.querySelectorAll("[data-publish-submit], [data-default-publish-submit]")
        .forEach((button) => { button.disabled = blocked; });
      restoreButton.disabled = sending || !currentSessionIsValid() || stored?.state === "conflict";
      newButton.disabled = sending || !currentSessionIsValid();
    }
    function showRecovery(message) {
      status.hidden = false;
      status.textContent = message;
      recoveryActions.hidden = false;
      if (originalDetails) originalDetails.hidden = !stored?.payload;
      if (originalFields && stored?.payload) {
        originalFields.replaceChildren();
        const labels = { title: "Title", description: "Description", location: "Location", start_time: "Start", expected_end_time: "Expected end", expire_minutes: "Recruiting minutes" };
        payloadEntries(stored.payload).forEach(([name, value]) => {
          if (!labels[name]) return;
          const term = document.createElement("dt");
          const detail = document.createElement("dd");
          term.textContent = labels[name];
          if (name === "location") {
            const option = Array.from(form.elements.location?.options || []).find((item) => item.value === String(value));
            detail.textContent = option?.textContent?.trim() || String(value);
          } else detail.textContent = String(value);
          originalFields.append(term, detail);
        });
      }
      syncControls();
    }
    function freshRequest() {
      requestInput.value = createUuid();
      stored = { requestId: requestInput.value, state: "draft" };
      writeSessionJson(storageKey, stored);
      recoveryActions.hidden = true;
      if (originalDetails) originalDetails.hidden = true;
      syncControls();
    }

    if (stored.state === "unknown" && stored.payload) {
      stored.fingerprint = createEntriesFingerprint(payloadEntries(stored.payload));
      // The immutable earlier request and the editable later draft have separate jobs.
      // Restore the request only when there is no ordinary draft to protect.
      if (!readSessionJson(draftKey)?.entries) {
        restoreFormPayload(form, stored.payload);
        updateCreatePreview();
      }
      showRecovery("An earlier publication is unconfirmed. Your current edits are kept. Check the earlier publication using its original details, or explicitly start a new request.");
    } else if (stored.state === "conflict") {
      showRecovery("This request ID conflicts with an earlier card. Your edits are kept; explicitly start a new request if you want to publish them.");
    }
    writeSessionJson(storageKey, stored);
    syncControls();

    function onEdit() {
      if (stored?.state === "unknown") {
        showRecovery("The earlier card may already be live. Your newer edits are saved separately. Check the earlier publication before starting a new request.");
      } else if (["validation", "failed"].includes(stored?.state) && stored.fingerprint !== currentFingerprint()) {
        freshRequest();
        status.hidden = false;
        status.textContent = "The earlier attempt was unsuccessful. Your edits are ready as a fresh request.";
      }
      syncControls();
    }
    form.addEventListener("input", onEdit);
    form.addEventListener("change", onEdit);
    form.addEventListener("plusone:createclear", () => {
      freshRequest();
      status.hidden = true;
      writeSessionJson(storageKey, null);
    });
    newButton.addEventListener("click", () => {
      if (sending || !currentSessionIsValid()) return;
      freshRequest();
      status.hidden = false;
      status.textContent = "New request ready for the current edits. The earlier uncertain request may already have created a card.";
    });

    async function sendRequest(request) {
      if (sending || !currentSessionIsValid()) return;
      sending = true;
      stored = { ...request, state: "sending" };
      writeSessionJson(storageKey, stored);
      form.setAttribute("aria-busy", "true");
      status.hidden = false;
      status.textContent = "Checking this publication… Your current edits remain available.";
      syncControls();
      const body = new FormData();
      payloadEntries(request.payload).forEach(([name, value]) => body.append(name, value));
      body.set("action", "publish");
      body.set("request_id", request.requestId);
      body.set("draft_revision", String(request.draftRevision || 0));
      body.set("session_scope", initialSessionScope);
      body.set("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]")?.value || "");
      try {
        const response = await fetchWithTimeout(form.getAttribute("action") || window.location.href, {
          method: "POST", body, credentials: "same-origin", headers: { Accept: "text/html, application/json" },
        }, 15000);
        if (!currentSessionIsValid()) return;
        if (response.status >= 500) throw new Error("unconfirmed publish response");
        if (response.redirected) {
          writeSessionJson(storageKey, null);
          const laterDraft = readSessionJson(draftKey);
          if (!publishedRequestLeavesDraft(laterDraft, request)) writeSessionJson(draftKey, null);
          window.location.assign(response.url);
          return;
        }
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("text/html")) {
          const html = await response.text();
          stored = { ...request, state: response.status === 409 ? "conflict" : "validation" };
          writeSessionJson(storageKey, stored);
          if (request.fingerprint !== currentFingerprint()) {
            if (response.status === 409) showRecovery("The earlier request conflicts with an existing card. Your current edits are kept. Review the original card before starting another request.");
            else {
              freshRequest();
              status.hidden = false;
              status.textContent = "The earlier card was not published. Your newer edits are kept; review them before publishing.";
            }
            return;
          }
          document.open();
          document.write(html);
          document.close();
          return;
        }
        let data = {};
        try { data = await response.json(); } catch (_error) { /* use the status fallback */ }
        if (data.identity_changed) {
          document.dispatchEvent(new CustomEvent("plusone:sessionscope", { detail: "identity-changed" }));
          return;
        }
        stored = { ...request, state: response.status === 409 ? "conflict" : "failed" };
        writeSessionJson(storageKey, stored);
        if (response.status === 409) showRecovery(data.error || "The earlier request conflicts with an existing card. Your edits are kept.");
        else {
          if (request.fingerprint !== currentFingerprint()) freshRequest();
          status.hidden = false;
          status.textContent = data.error || "The card was not published. Your current edits remain available.";
        }
      } catch (_error) {
        stored = { ...request, state: "unknown" };
        writeSessionJson(storageKey, stored);
        showRecovery("The earlier publication result is unknown. Your current edits are kept. Check the earlier publication to retry its exact details safely.");
      } finally {
        sending = false;
        syncControls();
        form.removeAttribute("aria-busy");
      }
    }
    restoreButton.addEventListener("click", () => {
      if (stored?.payload && stored.state !== "conflict") sendRequest({ ...stored });
    });
    form.addEventListener("submit", (event) => {
      if (event.submitter && event.submitter.value !== "publish") return;
      event.preventDefault();
      if (sending || !currentSessionIsValid()) return;
      if (stored?.state === "conflict" || (stored?.state === "unknown" && stored.fingerprint !== currentFingerprint())) {
        showRecovery("Check the earlier publication, or explicitly start a new request for these edits.");
        return;
      }
      const entries = Array.from(new FormData(form).entries())
        .filter(([name]) => name === "confirm_date" || reviewedPayloadEntries([[name, ""]]).length);
      const request = {
        requestId: requestInput.value || createUuid(), payload: JSON.stringify(entries), fingerprint: createEntriesFingerprint(entries),
        draftRevision: numericId(form.querySelector("[data-create-draft-revision]")?.value),
        assistText: form.querySelector("[name=assist_text]")?.value || "", state: "sending",
      };
      sendRequest(request);
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
    const canChat = currentSessionIsValid() && root.dataset.chatStatus === "chatting" && root.dataset.chatActive !== "false";
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

  function updateClosedNotice(root, payload, phase, open) {
    const closedNotice = root.querySelector("[data-closed-notice]");
    closedNotice.hidden = open;
    if (!open) {
      closedNotice.textContent = payload.closure_notice || (phase === "agreed"
        ? "Chat complete. Review the current meeting plan before travelling."
        : "This match has ended. No meetup was confirmed. Your history and safety report remain available.");
    }
    const consequence = root.querySelector("[data-report-consequence]");
    if (consequence) {
      consequence.textContent = phase === "waiting"
        ? "Reporting ends this waiting room for both people. No meetup will be confirmed."
        : phase === "chatting" ? "Reporting ends this chat for both people. No meetup will be confirmed."
          : phase === "agreed" && payload.plan?.meetup_status === "confirmed"
            ? "Reporting cancels this confirmed meetup. Neither person should travel based on the plan."
            : "Reporting records a safety concern about this history. It does not change an already ended or cancelled meetup.";
    }
  }

  function applyChatPhase(root, payload, isSending = false) {
    if (!currentSessionIsValid()) return root.dataset.chatStatus;
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
    updateClosedNotice(root, payload, phase, open);
    const retryPanel = root.querySelector("[data-waiting-retry]");
    if (retryPanel) {
      retryPanel.hidden = phase !== "expired";
      const retry = payload.waiting_retry || {};
      retryPanel.querySelector("[data-waiting-retry-reason]").textContent = retry.reason || "This waiting room has ended. Browse other plans, or refresh to check available next steps.";
      retryPanel.querySelector("[data-waiting-retry-form]").hidden = !retry.can_retry;
      const later = retryPanel.querySelector("[data-later-waiting-attempt]");
      later.hidden = !retry.later_attempt_url;
      if (retry.later_attempt_url) later.setAttribute("href", retry.later_attempt_url);
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
    const waitingReason = root.querySelector("[data-waiting-reason]");
    if (waitingReason) {
      const message = waitingReasonMessage(payload.waiting_reason);
      waitingReason.hidden = phase !== "waiting" || !message;
      waitingReason.textContent = message;
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

  function planActionAllowed(plan, action, outcome = "") {
    if (action === "outcome" && ["met", "not_met"].includes(outcome)) {
      const field = outcome === "met" ? "can_met" : "can_not_met";
      if (plan && field in plan) return Boolean(plan[field]);
    }
    const permission = {
      update_plan: "can_edit", confirm_plan: "can_confirm", arrived: "can_arrive",
      delayed: "can_delay", coordination: "can_coordinate", cancel_meetup: "can_cancel", outcome: "can_outcome", reopen_card: "can_reopen_card",
    }[action];
    return Boolean(permission && plan?.[permission]);
  }

  function waitingReasonMessage(reason) {
    return ({
      viewer_busy: "You already have a live chat. Finish or leave that chat before this one can start. Its waiting deadline keeps running.",
      other_busy: "Your Plus One is finishing another chat. This chat can start when you are both available. Its waiting deadline keeps running.",
      both_busy: "You are both in another live chat. Finish or leave those chats, then return here. Its waiting deadline keeps running.",
    })[reason] || "";
  }

  function meetupParticipantLabel(status, delayMinutes, outcome, details = null, nowMs = Date.now() + serverClockOffsetMs) {
    if (outcome === "met") return "Reported: we met";
    if (outcome === "not_met") return "Reported: we didn't meet";
    if (status === "delayed" && details?.arrival_eta && !details.historical) {
      const eta = Date.parse(details.arrival_eta);
      if (Number.isFinite(eta) && eta <= nowMs) return details.is_viewer
        ? "Arrival estimate has passed. Update your estimate." : "Arrival estimate has passed. Awaiting a fresh update.";
    }
    if (details?.arrival_label) return details.arrival_label;
    if (status === "arrived") return "Marked arrived";
    if (status === "delayed") return "Delay reported; arrival estimate not recorded.";
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

  function changedPlanFields(previous, current) {
    if (!previous || !current) return [];
    return ["meeting_point", "meeting_at_input", "expected_end_at_input"]
      .filter((name) => String(previous[name] || "") !== String(current[name] || ""));
  }

  function syncPlanDateBounds(form, plan) {
    if (!form?.elements || !plan) return;
    const start = form.elements.meeting_at;
    const end = form.elements.expected_end_at;
    if (!start || !end) return;
    start.min = plan.earliest_meeting_at_input || "";
    start.max = plan.latest_end_at_input || "";
    end.min = start.value || "";
    end.max = plan.latest_end_at_input || "";
  }

  function setupSharedPlanSummary() {
    const root = document.querySelector("[data-chat-root]");
    if (!root) return;
    const key = sessionScopedKey(`plan-summary:${root.dataset.chatId}`);
    let previous = readSessionJson(key);
    let changed = new Set(previous?.changed_fields || []);
    let label = previous?.change_label || "Adjusted";
    const initialChanges = Array.from(root.querySelectorAll("[data-plan-change-field]"))
      .filter((node) => !node.querySelector("[data-plan-changed-marker]")?.hidden)
      .map((node) => node.dataset.planChangeField);
    function update(plan) {
      if (!plan || (previous && numericId(plan.revision) < numericId(previous.revision))) return;
      if (previous && numericId(plan.revision) > numericId(previous.revision)) {
        changed = new Set(changedPlanFields(previous, plan));
        label = "Changed";
      } else if (!previous) changed = new Set(initialChanges);
      root.querySelectorAll("[data-plan-change-field]").forEach((node) => {
        const marker = node.querySelector("[data-plan-changed-marker]");
        if (!marker) return;
        marker.hidden = !changed.has(node.dataset.planChangeField);
        marker.textContent = label;
      });
      const deadline = root.querySelector("[data-feedback-deadline]");
      if (deadline && plan.feedback_deadline_display) deadline.textContent = plan.feedback_deadline_display;
      previous = { revision: plan.revision, meeting_point: plan.meeting_point,
        meeting_at_input: plan.meeting_at_input, expected_end_at_input: plan.expected_end_at_input,
        changed_fields: Array.from(changed), change_label: label };
      writeSessionJson(key, previous);
    }
    root.addEventListener("plusone:planupdate", (event) => update(event.detail));
    try { update(JSON.parse(document.getElementById("meetup-initial-payload")?.textContent || "null")?.plan); }
    catch (_error) { /* polling will supply the latest plan */ }
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
    const requestKey = sessionScopedKey(`plan-request:${root.dataset.chatId}`);
    const draftKey = sessionScopedKey(`plan-edits:${root.dataset.chatId}`);
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

    function renderArrivalUpdates() {
      if (!currentPlan) return;
      for (const actor of ["viewer", "other"]) {
        textAll(`[data-${actor}-meetup-status]`, meetupParticipantLabel(currentPlan[`${actor}_status`], currentPlan[`${actor}_delay_minutes`], currentPlan[`${actor}_outcome`], {
          arrival_label: currentPlan[`${actor}_arrival_label`], arrival_eta: currentPlan[`${actor}_arrival_eta`],
          is_viewer: actor === "viewer",
          historical: ["cancelled", "finished"].includes(currentPlan.meetup_status),
        }));
        const updated = root.querySelector(`[data-${actor}-arrival-updated]`);
        if (updated) {
          updated.hidden = !currentPlan[`${actor}_arrival_updated_at_display`];
          updated.textContent = currentPlan[`${actor}_arrival_updated_at_display`] ? `Updated ${currentPlan[`${actor}_arrival_updated_at_display`]}` : "";
        }
      }
    }

    function setRequestStatus(message, canRetry = false, kind = "notice") {
      if (statusTimer !== null) clearTimeout(statusTimer);
      statusTimer = null;
      recovery.hidden = !message;
      recovery.dataset.statusKind = kind;
      status.textContent = message;
      retry.hidden = !canRetry;
      retry.disabled = sending || !currentSessionIsValid();
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
        const outcome = button.closest("form")?.elements.outcome?.value || "";
        button.disabled = !currentSessionIsValid() || sending || Boolean(pending) || !planActionAllowed(currentPlan, action, outcome)
          || (action === "delayed" && ["5", "10"].includes(button.value) && !currentPlan?.[`can_delay_${button.value}`])
          || (action === "confirm_plan" && editingDirty)
          || (["update_plan", "confirm_plan"].includes(action) && root.dataset.chatActive === "false");
      });
      syncChatSendControls(root, root.dataset.chatSendBusy === "true");
      retry.disabled = sending || !currentSessionIsValid();
    }

    function restoreLatestInputs() {
      if (!currentPlan) return;
      editForm.elements.meeting_point.value = currentPlan.meeting_point || "";
      editForm.elements.meeting_at.value = currentPlan.meeting_at_input || "";
      editForm.elements.expected_end_at.value = currentPlan.expected_end_at_input || "";
      syncPlanDateBounds(editForm, currentPlan);
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
      const scheduleConflict = root.querySelector("[data-schedule-conflict]");
      if (scheduleConflict) scheduleConflict.hidden = !plan.schedule_conflict;
      const calendar = root.querySelector("[data-meetup-calendar]");
      if (calendar) calendar.hidden = plan.meetup_status !== "confirmed";
      syncPlanDateBounds(editForm, plan);
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
      renderArrivalUpdates();
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
      const clearArrival = root.querySelector("[data-clear-arrival]");
      if (clearArrival) clearArrival.hidden = plan.viewer_status !== "arrived";
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
    // A cached estimate cannot remain a future promise during a failed poll.
    setInterval(renderArrivalUpdates, 1000);

    if (savedEdits?.entries || pending?.entries?.some(([name]) => PLAN_EDIT_FIELDS.includes(name))) {
      const entries = savedEdits?.entries || pending.entries;
      restoreFormPayload(editForm, JSON.stringify(entries.filter(([name]) => !["csrfmiddlewaretoken", "request_id"].includes(name))));
      syncPlanDateBounds(editForm, currentPlan);
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
      syncPlanDateBounds(editForm, currentPlan);
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
      syncPlanDateBounds(editForm, currentPlan);
      writeSessionJson(draftKey, { entries: currentEditEntries() });
      showEditWarning("Your edits will replace the latest shared plan and ask both people to agree again.");
      syncActions();
    });

    function mutationBoundary() {
      root.dataset.planMutationGeneration = String(numericId(root.dataset.planMutationGeneration) + 1);
    }

    async function sendAction(request) {
      if (sending || !currentSessionIsValid()) return;
      sending = true;
      pending = { ...request, state: "sending" };
      writeSessionJson(requestKey, pending);
      mutationBoundary();
      syncActions();
      setRequestStatus("Saving your action…");
      const body = new FormData();
      request.entries.forEach(([name, value]) => body.append(name, value));
      body.set("request_id", request.requestId);
      body.set("session_scope", initialSessionScope);
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
            coordination: "Your arrival update is recorded. The agreed point and time stay the same.",
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
        if (sending || pending || !currentPlan || !currentSessionIsValid()) return;
        const action = form.elements.action.value;
        const outcome = form.elements.outcome?.value || "";
        if (!planActionAllowed(currentPlan, action, outcome) || (action === "confirm_plan" && editingDirty)) return;
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
    const storageKey = sessionScopedKey(`chat-draft:${root.dataset.chatId}`);
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
      if (!currentSessionIsValid()) { pollLoop.stop(); return; }
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
      if (!currentSessionIsValid() || isSending || !draft?.text || !draft.requestId) return;
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
      body.set("session_scope", initialSessionScope);
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

  function openerClickEntries(dataset) {
    if (!dataset?.batchId || !/^\d+$/.test(String(dataset.openerIndex ?? ""))) return null;
    return [["index", String(dataset.openerIndex)], ["batch_id", String(dataset.batchId)]];
  }

  function trackOpenerClick(button) {
    const container = button.closest("[data-opener-track-url]");
    const entries = openerClickEntries(button.dataset);
    if (!container || !entries || !currentSessionIsValid()) return;
    const csrf = document.querySelector("[name=csrfmiddlewaretoken]");
    if (!csrf) return;
    const body = new FormData();
    entries.forEach(([name, value]) => body.append(name, value));
    body.append("csrfmiddlewaretoken", csrf.value);
    body.append("session_scope", initialSessionScope);
    fetch(container.dataset.openerTrackUrl, { method: "POST", body, credentials: "same-origin" }).catch(() => {});
  }

  function setupPresence() {
    const root = document.querySelector("[data-chat-root]");
    if (!root?.dataset.presenceEndpoint) return;
    const csrf = root.querySelector("[name=csrfmiddlewaretoken]")?.value;
    if (!csrf) return;
    const presence = createPresenceTracker();

    function presenceBody(visible) {
      const lease = presence.next(visible);
      const body = new FormData();
      body.set("visible", visible ? "true" : "false");
      body.set("tab_id", lease.tab_id);
      body.set("sequence", String(lease.sequence));
      body.set("csrfmiddlewaretoken", csrf);
      body.set("session_scope", initialSessionScope);
      return body;
    }

    async function postVisible() {
      if (!currentSessionIsValid() || (root.dataset.chatStatus !== "waiting" && root.dataset.chatStatus !== "chatting")) return;
      const response = await fetchWithTimeout(root.dataset.presenceEndpoint, {
        method: "POST",
        body: presenceBody(true),
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      }, 4000);
      if (!response.ok) throw new Error(`presence ${response.status}`);
    }

    function postHidden() {
      if (!currentSessionIsValid() || (root.dataset.chatStatus !== "waiting" && root.dataset.chatStatus !== "chatting")) return;
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
    document.addEventListener("plusone:sessionchanged", () => loop.stop());
  }

  function createPresenceTracker(tabId = createUuid()) {
    // A per-document ID stays independent even when the browser clones tab storage.
    let sequence = 0;
    return { next: (visible) => ({ tab_id: tabId, sequence: ++sequence, visible: Boolean(visible) }) };
  }

  function dialogTabTarget(elements, activeElement, reverse = false) {
    if (!elements.length) return null;
    const index = elements.indexOf(activeElement);
    if (index === -1) return elements[reverse ? elements.length - 1 : 0];
    return elements[(index + (reverse ? -1 : 1) + elements.length) % elements.length];
  }

  function setupMatchDialog() {
    document.querySelectorAll("dialog[data-match-dialog]").forEach((dialog) => {
      const launcher = Array.from(document.querySelectorAll("[data-open-match-dialog]"))
        .find((button) => !button.dataset.dialogTarget || button.dataset.dialogTarget === dialog.id);
      function open() {
        if (dialog.open) return;
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
      }
      launcher?.addEventListener("click", open);
      dialog.addEventListener("keydown", (event) => {
        if (!dialog.open || event.key !== "Tab" || event.altKey || event.ctrlKey || event.metaKey) return;
        const focusable = Array.from(dialog.querySelectorAll("a[href], button, input, select, textarea, [tabindex]"))
          .filter((node) => node.tabIndex >= 0 && !node.matches(":disabled")
            && !node.closest("[hidden], [inert]") && node.getClientRects().length > 0
            && getComputedStyle(node).visibility !== "hidden");
        const target = dialogTabTarget(focusable, document.activeElement, event.shiftKey);
        event.preventDefault();
        if (target) target.focus();
        else { dialog.setAttribute("tabindex", "-1"); dialog.focus(); }
      });
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        dialog.close();
      });
      dialog.addEventListener("close", () => launcher?.focus());
      if (dialog.open && typeof dialog.showModal === "function") {
        dialog.removeAttribute("open");
        open();
      }
    });
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

  function decodeVapidPublicKey(value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9_-]+={0,2}$/.test(value)) throw new Error("Background alerts have an invalid public key.");
    const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - base64.length % 4) % 4);
    const decoded = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
    if (decoded.length !== 65 || decoded[0] !== 4) throw new Error("Background alerts have an invalid public key.");
    return decoded;
  }

  function sameOriginPushUrl(value, origin) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const url = new URL(value, origin);
      return url.origin === origin && ["https:", "http:"].includes(url.protocol) ? url.href : null;
    } catch (_error) { return null; }
  }

  function readAlertPreference(value) {
    if (value === "enabled") return { enabled: true, push_endpoint: "", push_confirmed: false };
    try {
      const parsed = JSON.parse(value || "null");
      return { enabled: parsed?.enabled === true, push_endpoint: typeof parsed?.push_endpoint === "string" ? parsed.push_endpoint : "", push_confirmed: parsed?.push_confirmed === true };
    } catch (_error) { return { enabled: false, push_endpoint: "", push_confirmed: false }; }
  }

  async function enablePushDelivery({ serviceWorker, publicKey, subscribeUrl, post, beforeReplace, onSubscription, workerUrl = "/service-worker.js" }) {
    const applicationServerKey = decodeVapidPublicKey(publicKey);
    let registration = await serviceWorker.register(workerUrl, { scope: "/" });
    if (!registration.active || registration.active.state !== "activated") {
      let timer;
      try {
        registration = await Promise.race([
          serviceWorker.ready,
          new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error("Background alerts could not start. Try again.")), 10000); }),
        ]);
      } finally { clearTimeout(timer); }
    }
    let subscription = await registration.pushManager.getSubscription();
    const existingKey = subscription?.options?.applicationServerKey;
    if (existingKey && (new Uint8Array(existingKey).length !== applicationServerKey.length
      || new Uint8Array(existingKey).some((value, index) => value !== applicationServerKey[index]))) {
      if (!beforeReplace) throw new Error("The previous background subscription needs to be closed first.");
      await beforeReplace(subscription.endpoint);
      await subscription.unsubscribe();
      subscription = null;
    }
    if (!subscription) subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
    if (onSubscription) onSubscription(subscription);
    await post(subscribeUrl, { subscription: subscription.toJSON() });
    return subscription;
  }

  async function deactivatePushDelivery({ serviceWorker, endpoint, unsubscribeUrl, post }) {
    let subscription = null;
    try {
      const registration = await serviceWorker?.getRegistration("/");
      subscription = await registration?.pushManager.getSubscription();
    } catch (_error) { /* a revoked browser permission can leave only the stored endpoint */ }
    const endpoints = new Set([endpoint, subscription?.endpoint].filter(Boolean));
    for (const item of endpoints) await post(unsubscribeUrl, { endpoint: item });
    // Never abandon the browser subscription while server delivery is still active.
    try {
      if (subscription) await subscription.unsubscribe();
      return { server_deactivated: true, browser_unsubscribed: true };
    } catch (_error) { return { server_deactivated: true, browser_unsubscribed: false }; }
  }

  function setupBrowserNotifications() {
    const button = document.querySelector("[data-enable-notifications]");
    const retry = document.querySelector("[data-retry-background-alerts]");
    const status = document.querySelector("[data-notification-status]");
    const settingKey = sessionScopedKey("browser-notifications");
    const available = typeof globalThis.Notification !== "undefined" && globalThis.isSecureContext !== false;
    const pushSupported = available && "serviceWorker" in navigator && "PushManager" in globalThis;
    let preference = { enabled: false, push_endpoint: "", push_confirmed: false };
    let pushConfig = null;
    let pushActive = false;
    let operationBusy = false;
    let setupError = "";
    let lastResult = "";
    try { preference = readAlertPreference(localStorage.getItem(settingKey)); } catch (_error) { /* optional preference */ }
    function enabled() { return available && preference.enabled && Notification.permission === "granted"; }
    function savePreference() {
      try { localStorage.setItem(settingKey, JSON.stringify(preference)); } catch (_error) { /* current tab still works */ }
    }
    function render() {
      if (!button) return;
      button.disabled = !available || !currentSessionIsValid() || operationBusy;
      button.textContent = operationBusy ? "Updating alerts…" : preference.enabled ? "Turn off browser alerts" : "Enable browser alerts";
      if (retry) {
        retry.hidden = !enabled() || !pushConfig?.enabled || !pushSupported || pushActive || (!setupError && !preference.push_endpoint);
        retry.disabled = operationBusy || !currentSessionIsValid();
      }
      if (status) {
        status.hidden = false;
        status.textContent = !available ? "This browser cannot show alerts. Check Updates when you return."
          : operationBusy ? "Updating your browser alert settings…"
            : lastResult || (pushActive && enabled() ? "Background alerts are subscribed on this device. Delivery depends on your browser, device settings and connection; always check Updates when you return."
          : enabled() ? (preference.push_endpoint && pushConfig?.enabled !== false
            ? `Background setup could not be confirmed. Alerts may be active on this device; retry or turn them off to reconcile. In-page Updates remain available.${setupError ? ` ${setupError}` : ""}`
            : `Alerts can appear while this app is open. Background tabs may be delayed; there are no alerts after the app is closed.${setupError ? ` Background setup did not complete: ${setupError}` : ""}`)
            : Notification.permission === "denied" ? "Browser alerts are blocked. You can change this in your browser's site settings. Updates remain available here."
              : pushConfig?.enabled && pushSupported ? "Optional background alerts on this device. You choose whether to grant permission."
                : "Background delivery is not enabled here. Optional alerts work while this app is open. Check Updates after returning to the app.");
      }
    }
    async function postPush(url, payload) {
      const target = sameOriginPushUrl(url, window.location.origin);
      if (!target) throw new Error("Alert settings are unavailable.");
      const response = await fetchWithTimeout(target, {
        method: "POST", credentials: "same-origin",
        headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "" },
        body: JSON.stringify({ ...payload, session_scope: initialSessionScope }),
      }, 10000);
      let data = {};
      try { data = await response.json(); } catch (_error) { /* status still establishes whether the write was accepted */ }
      if (!response.ok) throw new Error(data.error || "The alert settings were not saved. Try again.");
      if (data.ok !== true) throw new Error("The alert settings could not be confirmed. Retry to reconcile them.");
      return data;
    }
    const configReady = (async () => {
      try {
        const configUrl = document.body.dataset.pushConfigUrl || "/notifications/push/config/";
        const target = sameOriginPushUrl(configUrl, window.location.origin);
        if (!target) return;
        const response = await fetchWithTimeout(target, { credentials: "same-origin", headers: { Accept: "application/json" } }, 4000);
        if (!response.ok) return;
        const data = await response.json();
        pushConfig = { ...data, subscribe_url: sameOriginPushUrl(data.subscribe_url, window.location.origin), unsubscribe_url: sameOriginPushUrl(data.unsubscribe_url, window.location.origin) };
        pushActive = Boolean(pushConfig.enabled && pushSupported && preference.enabled && preference.push_endpoint && preference.push_confirmed);
      } catch (_error) { /* in-page alerts remain available without a push service */ }
      finally { render(); }
    })();
    async function subscribeBackground() {
      await configReady;
      if (!pushConfig?.enabled || !pushSupported || !currentSessionIsValid()) return false;
      if (!pushConfig.subscribe_url || !pushConfig.unsubscribe_url) throw new Error("Background alert settings are incomplete.");
      preference.push_confirmed = false;
      savePreference();
      const subscription = await enablePushDelivery({
        serviceWorker: navigator.serviceWorker, publicKey: pushConfig.public_key,
        subscribeUrl: pushConfig.subscribe_url, post: postPush,
        beforeReplace: (endpoint) => postPush(pushConfig.unsubscribe_url, { endpoint }),
        onSubscription: (subscription) => { preference.push_endpoint = subscription.endpoint; savePreference(); },
      });
      preference.push_endpoint = subscription.endpoint;
      preference.push_confirmed = true;
      pushActive = true;
      return true;
    }
    button?.addEventListener("click", async () => {
      if (!available || !currentSessionIsValid() || operationBusy) return;
      const disabling = preference.enabled;
      // Start the permission request directly in the user's click gesture.
      const permissionRequest = disabling ? null : Notification.requestPermission();
      operationBusy = true;
      lastResult = "";
      render();
      try {
        if (disabling) {
          await configReady;
          if (preference.push_endpoint) {
            if (!pushConfig?.unsubscribe_url) throw new Error("Background delivery could not be disabled. Retry before assuming alerts are off.");
            const result = await deactivatePushDelivery({ serviceWorker: navigator.serviceWorker, endpoint: preference.push_endpoint, unsubscribeUrl: pushConfig.unsubscribe_url, post: postPush });
            if (!result.browser_unsubscribed) lastResult = "Server delivery is disabled. Browser subscription cleanup did not complete; check the browser's site settings.";
          }
          preference = { enabled: false, push_endpoint: "", push_confirmed: false };
          pushActive = false;
          lastResult ||= "Browser alerts are off. Your Updates remain available.";
        } else if (await permissionRequest === "granted" && currentSessionIsValid()) {
          preference.enabled = true;
          setupError = "";
          try { await subscribeBackground(); }
          catch (error) { pushActive = false; setupError = error.message; }
        }
        savePreference();
      } catch (error) {
        lastResult = disabling ? `Alerts have not been turned off: ${error.message}` : "The browser did not enable alerts. Updates remain available here.";
      } finally {
        operationBusy = false;
        render();
        document.dispatchEvent(new CustomEvent("plusone:notificationpermission"));
      }
    });
    retry?.addEventListener("click", async () => {
      if (!enabled() || operationBusy || !currentSessionIsValid()) return;
      operationBusy = true;
      lastResult = "";
      render();
      try { await subscribeBackground(); setupError = ""; savePreference(); }
      catch (error) { setupError = error.message; }
      finally { operationBusy = false; render(); document.dispatchEvent(new CustomEvent("plusone:notificationpermission")); }
    });
    window.addEventListener("storage", (event) => {
      if (event.key !== settingKey) return;
      preference = readAlertPreference(event.newValue);
      pushActive = Boolean(pushConfig?.enabled && pushSupported && preference.enabled && preference.push_endpoint && preference.push_confirmed);
      lastResult = "";
      render();
      document.dispatchEvent(new CustomEvent("plusone:notificationpermission"));
    });
    document.addEventListener("plusone:sessionchanged", () => {
      preference = { enabled: false, push_endpoint: "", push_confirmed: false };
      pushActive = false;
      try { localStorage.removeItem(settingKey); } catch (_error) { /* old identity is already disabled */ }
    });
    render();
    function emit(message, url, tag) {
      if (!enabled() || pushActive || !currentSessionIsValid()) return;
      try {
        const notification = new Notification("Plus One", { body: message, tag: `${initialSessionScope}:${tag}` });
        notification.addEventListener("click", () => {
          window.focus();
          if (url && new URL(url, window.location.href).origin === window.location.origin) window.location.assign(url);
          notification.close();
        });
      } catch (_error) { /* some browsers require Web Push; inbox remains available */ }
    }
    return { enabled: () => enabled() && !pushActive, emit };
  }

  function matchUpdateMessage(match) {
    const label = ({ waiting: "A match is waiting", chatting: "Your chat is ready", agreed: "Your meeting plan is confirmed" })[match?.status] || "Your match was updated";
    return `${label}: ${match?.title || "Plus One"}`;
  }

  function inboxAttentionItems(matches, notifications) {
    const rows = new Map();
    const current = (notifications || []).filter((item) => item.is_current !== false
      && (item.attention_required === true || (!item.is_read && item.attention_required !== false)));
    current.sort((left, right) => Number(right.kind === "task") - Number(left.kind === "task"));
    current.forEach((item) => {
      const key = item.match_id ? `match:${item.match_id}` : item.url ? `url:${item.url}` : `notice:${item.id}`;
      if (!rows.has(key)) rows.set(key, { key, message: item.message || item.title || "Your match was updated", url: item.url });
    });
    (matches || []).forEach((match) => {
      if (!["waiting", "chatting"].includes(match.status)) return;
      const key = `match:${match.id}`;
      if (rows.has(key) || (match.url && Array.from(rows.values()).some((item) => item.url === match.url))) return;
      rows.set(key, { key, message: matchUpdateMessage(match), url: match.url });
    });
    return Array.from(rows.values()).slice(0, 5);
  }

  function updateInboxList(list, items) {
    const focused = list.contains(document.activeElement) ? document.activeElement : null;
    const existing = new Map(Array.from(list.children).map((row) => [row.dataset.inboxKey, row]));
    const retained = new Set();
    items.forEach((item, index) => {
      let row = existing.get(item.key);
      if (!row) {
        row = document.createElement("li");
        row.dataset.inboxKey = item.key;
        row.append(document.createElement(item.url ? "a" : "span"));
      }
      let link = row.firstElementChild;
      if (Boolean(item.url) !== (link.tagName === "A")) {
        const replacement = document.createElement(item.url ? "a" : "span");
        link.replaceWith(replacement);
        link = replacement;
      }
      if (link.textContent !== item.message) link.textContent = item.message;
      if (item.url && link.getAttribute("href") !== item.url) link.setAttribute("href", item.url);
      retained.add(row);
      if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    });
    existing.forEach((row) => { if (!retained.has(row)) row.remove(); });
    if (focused?.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
    else if (focused && !focused.isConnected) {
      (list.querySelector("a") || document.querySelector("[data-dashboard-link]"))?.focus({ preventScroll: true });
    }
  }

  function setupSessionPolling() {
    const endpoint = document.body.dataset.sessionUpdatesUrl;
    if (!endpoint) return;
    let initialized = false;
    let previous = new Map();
    let previousNotifications = new Set();
    let loop;
    const alerts = setupBrowserNotifications();

    function renderInbox(matches, notifications) {
      const inbox = document.querySelector("[data-match-inbox]");
      const list = document.querySelector("[data-match-inbox-list]");
      if (!inbox || !list) return;
      const items = inboxAttentionItems(matches, notifications);
      inbox.hidden = items.length === 0;
      updateInboxList(list, items);
    }

    function updateBadge(link, count, label) {
      if (!link) return;
      let badge = link.querySelector(".nav-badge");
      if (!badge && count > 0) {
        badge = document.createElement("span");
        badge.className = "nav-badge";
        link.append(badge);
      }
      if (badge) {
        badge.textContent = String(count);
        badge.hidden = count === 0;
        badge.setAttribute("aria-label", `${count} ${label}`);
      }
    }

    async function fetchSession() {
      if (!currentSessionIsValid()) { loop.stop(); return; }
      const response = await fetchWithTimeout(endpoint, { credentials: "same-origin", headers: { Accept: "application/json" } }, 4000);
      if (!response.ok) throw new Error(`session ${response.status}`);
      const data = await response.json();
      if (data.session_scope && data.session_scope !== initialSessionScope) {
        document.dispatchEvent(new CustomEvent("plusone:sessionscope", { detail: data.session_scope }));
        loop.stop();
        return;
      }
      if (!data.authenticated) {
        document.dispatchEvent(new CustomEvent("plusone:sessionscope", { detail: "session-ended" }));
        loop.stop();
        return;
      }
      setServerClock(data.server_time);
      const matches = Array.isArray(data.matches) ? data.matches : [];
      const notifications = Array.isArray(data.notifications) ? data.notifications : [];
      const current = new Map(matches.map((match) => [String(match.id), match.status]));
      if (initialized) {
        const changed = matches.find((match) => !previous.has(String(match.id)) || previous.get(String(match.id)) !== match.status);
        const newNotice = notifications.find((item) => !item.is_read && item.is_current !== false
          && item.attention_required !== false && !previousNotifications.has(String(item.id)));
        if (newNotice) {
          const message = newNotice.message || newNotice.title || "Your match was updated";
          showToast(message, newNotice.url);
          alerts.emit(message, newNotice.url, `update:${newNotice.id}`);
        } else if (changed) {
          const message = matchUpdateMessage(changed);
          showToast(message, changed.url);
          alerts.emit(message, changed.url, `match:${changed.id}:${changed.status}`);
        }
      }
      initialized = true;
      previous = current;
      previousNotifications = new Set(notifications.map((item) => String(item.id)));
      updateBadge(document.querySelector("[data-dashboard-link]"), numericId(data.open_count), "open matches");
      updateBadge(document.querySelector("[data-notifications-link]"), numericId(data.unread_count), "unread updates");
      renderInbox(matches, notifications);
    }

    loop = createPollLoop({ task: fetchSession, isVisible: () => currentSessionIsValid() && (document.visibilityState !== "hidden" || alerts.enabled()), baseDelay: 5000, maxDelay: 20000 });
    loop.start();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" || alerts.enabled()) loop.start();
      else loop.stop();
    });
    document.addEventListener("plusone:sessionchanged", () => loop.stop());
    document.addEventListener("plusone:notificationpermission", () => loop.start());
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
    setupSessionScope();
    setupDiscoverExpiry();
    document.querySelectorAll("[data-js-only]").forEach((node) => { node.hidden = false; });
    updateCountdowns();
    setInterval(updateCountdowns, 1000);
    updateCreatePreview();
    setupCreateDraft();
    setupTimeClarifier();
    setupReliablePublish();
    setupSharedPlanSummary();
    setupMeetingPlan();
    setupChat();
    setupPresence();
    setupSessionPolling();
    setupSubmitConfirms();
    setupMatchDialog();
    ["id_title", "id_description", "id_activity_type", "id_location", "id_start_time", "id_expected_end_time", "id_expire_minutes"].forEach((id) => {
      const field = document.getElementById(id);
      field?.addEventListener("input", updateCreatePreview);
      field?.addEventListener("change", updateCreatePreview);
    });
  }

  const testApi = {
    matchingDeadlineState,
    updateDiscoverAvailability,
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
    sessionScopedKey,
    reviewedPayloadEntries,
    createEntriesFingerprint,
    restoreNewerCreateDraft,
    clearCreateFields,
    publishedRequestLeavesDraft,
    proposalEntries,
    previewStartTimeLabel,
    createPresenceTracker,
    waitingReasonMessage,
    matchUpdateMessage,
    decodeVapidPublicKey,
    sameOriginPushUrl,
    readAlertPreference,
    enablePushDelivery,
    deactivatePushDelivery,
    openerClickEntries,
    dialogTabTarget,
    changedPlanFields,
    syncPlanDateBounds,
    inboxAttentionItems,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = testApi;
  if (typeof window !== "undefined") window.PlusOneTest = testApi;
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", initialize);
})();

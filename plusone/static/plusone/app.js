function updateCountdowns() {
  const now = new Date();
  document.querySelectorAll(".countdown[data-deadline]").forEach((node) => {
    const deadline = new Date(node.dataset.deadline);
    const delta = deadline - now;
    if (Number.isNaN(deadline.getTime())) {
      node.textContent = "--";
      return;
    }
    if (delta <= 0) {
      node.textContent = "expired";
      node.classList.add("expired");
      node.classList.remove("urgent");
      return;
    }
    const totalSeconds = Math.floor(delta / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    node.textContent = `${minutes}m ${String(seconds).padStart(2, "0")}s`;
    node.classList.toggle("urgent", totalSeconds <= 60);
  });
}

function previewLocationLabel(locationInput) {
  if (!locationInput?.value) {
    return "Campus location";
  }
  const label = locationInput.selectedOptions?.[0]?.textContent?.trim();
  return label && label !== "---------" ? label : "Campus location";
}

function previewActivityLabel(activityInput) {
  if (!activityInput?.value) {
    return "Activity";
  }
  const label = activityInput.selectedOptions?.[0]?.textContent?.trim();
  return label && label !== "---------" ? label : "Activity";
}

function previewStartTimeLabel(value) {
  if (!value) {
    return "Start time";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Start time";
  }
  const month = date.toLocaleString(undefined, { month: "short" });
  const day = date.getDate();
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${month} ${day}, ${hours}:${minutes}`;
}

function updateCreatePreview() {
  // The preview mirrors the review form only; server-side AI parsing and
  // moderation remain the source of truth when the user submits.
  const titleInput = document.getElementById("id_title");
  const descriptionInput = document.getElementById("id_description");
  const activityInput = document.getElementById("id_activity_type");
  const locationInput = document.getElementById("id_location");
  const startInput = document.getElementById("id_start_time");
  const expireInput = document.getElementById("id_expire_minutes");
  const previewTitle = document.querySelector("[data-post-preview-title]");
  if (!previewTitle) return;

  const previewDescription = document.querySelector("[data-post-preview-description]");
  const previewActivity = document.querySelector("[data-post-preview-activity]");
  const previewLocation = document.querySelector("[data-post-preview-location]");
  const previewTime = document.querySelector("[data-post-preview-time]");
  const previewExpire = document.querySelector("[data-post-preview-expire]");
  const previewCard = previewTitle.closest(".activity-card");
  const selectedActivity = activityInput?.value || "other";

  previewTitle.textContent = titleInput?.value || "Your Plus One title";
  previewDescription.textContent = descriptionInput?.value || "The card preview updates as you edit the structured fields.";
  previewActivity.textContent = previewActivityLabel(activityInput);
  previewLocation.textContent = previewLocationLabel(locationInput);
  previewTime.textContent = previewStartTimeLabel(startInput?.value);
  previewExpire.textContent = expireInput?.value || "45";
  if (previewCard) {
    previewCard.classList.remove("color-food", "color-sports", "color-study", "color-club", "color-explore", "color-other");
    previewCard.classList.add(`color-${selectedActivity}`);
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

function appendChatMessage(list, message) {
  if (!message?.id || list.querySelector(`[data-message-id="${message.id}"]`)) {
    return;
  }

  const empty = list.querySelector("[data-empty-chat]");
  if (empty) {
    empty.remove();
  }

  const bubble = document.createElement("div");
  bubble.className = `bubble ${message.bubble_class}`;
  bubble.dataset.messageId = message.id;

  const meta = document.createElement("small");
  meta.textContent = `${message.sender_label} · ${message.created_at}${message.is_flagged ? " · flagged" : ""}`;

  const text = document.createElement("p");
  text.textContent = message.message;

  bubble.append(meta, text);
  list.append(bubble);
  list.dataset.lastMessageId = String(message.id);
  list.scrollTop = list.scrollHeight;
}

function setupChatPolling() {
  const panel = document.querySelector("[data-chat-endpoint]");
  const list = document.querySelector("[data-chat-messages]");
  if (!panel || !list) return;

  const endpoint = panel.dataset.chatEndpoint;
  const isChatting = panel.dataset.chatStatus === "chatting";
  const form = document.querySelector("[data-chat-form]");
  const input = form?.querySelector("input[name='message']");
  const submit = form?.querySelector("button[type='submit']");
  const warning = form?.querySelector("[data-chat-warning]");
  let pollTimer = null;

  function showChatWarning(message) {
    if (!warning) {
      window.alert(message);
      return;
    }
    warning.textContent = message;
    warning.hidden = false;
  }

  function clearChatWarning() {
    if (warning) {
      warning.textContent = "";
      warning.hidden = true;
    }
  }

  async function fetchMessages() {
    // Avoid background polling when the tab is hidden; the next visibility
    // change fetches missed messages using lastMessageId.
    if (document.visibilityState === "hidden") return;
    const after = list.dataset.lastMessageId || "0";
    try {
      const response = await fetch(`${endpoint}?after=${encodeURIComponent(after)}`, {
        headers: { "Accept": "application/json" },
      });
      if (!response.ok) return;
      const data = await response.json();
      if (data.chat_status && panel.dataset.chatStatus && data.chat_status !== panel.dataset.chatStatus) {
        window.location.reload();
        return;
      }
      data.messages?.forEach((message) => appendChatMessage(list, message));
      if (data.chat_active === false) {
        form?.remove();
        if (pollTimer) {
          clearInterval(pollTimer);
        }
      }
    } catch (error) {
      return;
    }
  }

  if (form && input) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;

      const formData = new FormData(form);
      if (submit) submit.disabled = true;
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          body: formData,
          headers: { "Accept": "application/json" },
        });
        const data = await response.json();
        if (response.ok && data.message) {
          appendChatMessage(list, data.message);
          input.value = "";
          clearChatWarning();
        } else if (data.warning || data.error) {
          showChatWarning(data.warning || data.error);
        }
      } finally {
        if (submit) submit.disabled = false;
        input.focus();
      }
    });
  }

  document.querySelectorAll("[data-quick-reply]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!input) return;
      input.value = button.dataset.quickReply || "";
      input.focus();
      clearChatWarning();
    });
  });

  if (!isChatting) return;

  // Ended/expired chats render their history once and do not keep polling.
  fetchMessages();
  pollTimer = setInterval(fetchMessages, 2000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      fetchMessages();
    }
  });
}

function setupSubmitConfirms() {
  document.querySelectorAll("[data-confirm-reset], [data-confirm-submit]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const message = form.dataset.confirmReset || form.dataset.confirmSubmit;
      if (message && !window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  updateCountdowns();
  setInterval(updateCountdowns, 1000);
  updateCreatePreview();
  setupTimeClarifier();
  setupChatPolling();
  setupSubmitConfirms();
  ["id_title", "id_description", "id_activity_type", "id_location", "id_start_time", "id_expire_minutes"].forEach((id) => {
    const field = document.getElementById(id);
    if (field) {
      field.addEventListener("input", updateCreatePreview);
      field.addEventListener("change", updateCreatePreview);
    }
  });
});

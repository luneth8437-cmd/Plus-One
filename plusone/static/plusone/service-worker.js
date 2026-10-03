(() => {
  "use strict";

  function notificationUrl(value, origin) {
    const fallback = new URL("/dashboard/", origin).href;
    try {
      const url = new URL(value || fallback, origin);
      if (url.origin !== origin || !["https:", "http:"].includes(url.protocol)) return fallback;
      if (!/^\/(?:chat\/\d+\/|dashboard\/|notifications\/)$/u.test(url.pathname)) return fallback;
      return url.href;
    } catch (_error) { return fallback; }
  }

  function notificationOptions(payload, origin) {
    const data = payload && typeof payload === "object" ? payload : {};
    return {
      title: typeof data.title === "string" ? data.title.slice(0, 160) : "Plus One",
      options: {
        body: typeof data.body === "string" ? data.body.slice(0, 500) : "Check your Plus One updates.",
        tag: typeof data.tag === "string" ? data.tag.slice(0, 200) : "plusone:updates",
        data: { url: notificationUrl(data.url, origin) },
      },
    };
  }

  async function openNotification(clients, value, origin) {
    const url = notificationUrl(value, origin);
    const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
    const existing = windows.find((client) => client.url === url);
    if (existing) return existing.focus();
    // Keep an unfinished draft in another tab intact when opening a plan.
    return clients.openWindow(url);
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { notificationUrl, notificationOptions, openNotification };
  }
  if (typeof self === "undefined" || typeof self.addEventListener !== "function") return;

  self.addEventListener("install", (event) => { event.waitUntil(self.skipWaiting()); });
  self.addEventListener("activate", (event) => { event.waitUntil(self.clients.claim()); });
  self.addEventListener("push", (event) => {
    let payload = {};
    try { payload = event.data?.json() || {}; } catch (_error) { /* malformed data still gives a safe visible update */ }
    const notification = notificationOptions(payload, self.location.origin);
    event.waitUntil(self.registration.showNotification(notification.title, notification.options));
  });
  self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    event.waitUntil(openNotification(self.clients, event.notification.data?.url, self.location.origin));
  });
  // No fetch handler: writes and pages are always served by the current session.
})();

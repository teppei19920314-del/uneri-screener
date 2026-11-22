/* 通知を受け取るための小さな仕組み(画面の保存などはしない) */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { body: e.data ? e.data.text() : "" }; }
  const url = new URL(d.url || "index.html#alerts", self.registration.scope).href;
  e.waitUntil(self.registration.showNotification(d.title || "うねり底", {
    body: d.body || "", tag: d.tag || "uneri", data: { url }, icon: "icon-180.png", badge: "icon-180.png",
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    for (const w of list) {
      if (w.url.startsWith(self.registration.scope)) {
        w.postMessage({ open: url });
        return w.focus();
      }
    }
    return self.clients.openWindow(url);
  }));
});

// 画面のファイル(HTML・JS・CSS)は毎回サーバーに新しい版があるか確かめる。
// iPhoneのホーム画面アプリが古い画面を10分ほど使い続けるのを防ぐ(保存はしない)。
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (!(req.mode === "navigate" || /\.(html|js|css|webmanifest)$/.test(url.pathname))) return;
  e.respondWith(fetch(url.href, { cache: "no-cache", credentials: "same-origin" }).catch(() => fetch(req)));
});

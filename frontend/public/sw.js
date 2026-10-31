/* KAN-201: push only. No fetch handler, offline shell or cached API responses. */
const OWNER_CACHE = 'team4-notification-owner-v1';
const OWNER_URL = new URL('/__notification_owner__', self.location.origin).href;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LINK = /^\/(?:cs\/tickets|tft\/projects)\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
async function owner() { const cache = await caches.open(OWNER_CACHE); const value = await cache.match(OWNER_URL); return value ? (await value.json()).id : null; }
async function bind(id) {
  const cache = await caches.open(OWNER_CACHE);
  if (id) await cache.put(OWNER_URL, new Response(JSON.stringify({ id })));
  else await cache.delete(OWNER_URL);
}
async function json(path) {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(10000), redirect: 'error' });
  if (!response.ok) throw new Error('Notification verification failed');
  return (await response.json()).data;
}
async function currentNotification(id, expectedOwner, unread = false) {
  if (!UUID.test(id || '') || !expectedOwner || await owner() !== expectedOwner) return null;
  const user = await json('/api/v1/platform/me');
  if (user?.id !== expectedOwner) return null;
  // Payload has no recipient ID. Verify against this session's real inbox, never a title/url alone.
  const inbox = await json('/api/v1/platform/notifications?limit=100&offset=0');
  const item = inbox?.items?.find(row => row.id === id && (!unread || row.read_at === null));
  if (!item || !LINK.test(item.link_path) || typeof item.title !== 'string' || !item.title || item.title.length > 300) return null;
  if (await owner() !== expectedOwner) return null;
  return item;
}
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('message', event => {
  if (event.data?.type !== 'NOTIFICATION_OWNER') return;
  event.waitUntil((async () => {
    const client = event.source?.id ? await self.clients.get(event.source.id) : null;
    if (!client || new URL(client.url).origin !== self.location.origin) return;
    const id = event.data.id;
    if (id !== null && !UUID.test(id || '')) return;
    // Never trust an old tab to bind an owner different from the currently authenticated user.
    if (id && (await json('/api/v1/platform/me'))?.id !== id) return;
    await bind(id);
    if (!id) for (const shown of await self.registration.getNotifications()) shown.close();
    event.ports[0]?.postMessage({ ok: true });
  })().catch(() => {}));
});
self.addEventListener('push', event => {
  event.waitUntil((async () => {
    const payload = event.data?.json();
    const id = await owner();
    const item = await currentNotification(payload?.id, id, true);
    if (!item) return;
    await self.registration.showNotification(item.title, { tag: `notification:${item.id}`, data: { id: item.id, ownerId: id } });
  })().catch(() => {}));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const data = event.notification.data;
    const item = await currentNotification(data?.id, data?.ownerId);
    if (!item) return;
    const target = new URL(item.link_path, self.location.origin).href;
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const same = windows.filter(client => new URL(client.url).origin === self.location.origin);
    const existing = same.find(client => new URL(client.url).href === target) || same[0];
    if (existing) { const opened = new URL(existing.url).href === target ? existing : await existing.navigate(target); if (opened) await opened.focus(); }
    else await self.clients.openWindow(target);
  })().catch(() => {}));
});

/* Service worker: cache vỏ ứng dụng (network-first, dự phòng khi mất mạng). Không cache dữ liệu API hay RSS. */
const CACHE = 'dailyhub-shell-v23';
const SHELL = ['./', 'index.html', 'style.css', 'script.js', 'overview.js', 'library.js', 'spider.html', 'manifest.json', 'icon.svg'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return;   // RSS/API bên ngoài không đi qua cache
  if (url.pathname.startsWith('/api/')) return;                                    // dữ liệu FreeLLMAPI luôn lấy mới
  const isShell = SHELL.some(p => url.pathname.endsWith('/' + p.replace('./', '')) || (p === './' && url.pathname.endsWith('/')));
  e.respondWith(fetch(e.request).then(res => {
    if (res.ok && isShell) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
    return res;
  }).catch(() => caches.match(e.request).then(m => m || caches.match('index.html'))));
});

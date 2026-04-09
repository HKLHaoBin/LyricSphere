// Service Worker for AMLL Web Player
const CACHE_NAME = "amll-web-player-v4";
const urlsToCache = [
  "/amll-web/",
  "/amll-web/index.html",
  "/amll-web/assets/icon-96x96.png",
  "/amll-web/assets/icon-512x512.png",
  "/amll-web/public/jsmediatags.min.js",
];

const NETWORK_FIRST_EXT = [".js", ".css", ".html", ".wasm"];

const CACHE_ALLOWLIST_PREFIXES = [
  "/amll-web/",
  "/amll-web/assets/",
  "/amll-web/public/",
];

function shouldCacheRequest(request, url) {
  if (url.origin !== self.location.origin) {
    return false;
  }
  if (request.method !== "GET") {
    return false;
  }
  const path = url.pathname || "";
  if (path === "/amll-web") {
    return true;
  }
  return CACHE_ALLOWLIST_PREFIXES.some((prefix) => path.startsWith(prefix));
}

function shouldUseNetworkFirst(request, url) {
  if (request.mode === "navigate") {
    return true;
  }
  const path = url.pathname || "";
  return NETWORK_FIRST_EXT.some((ext) => path.endsWith(ext));
}

// 安装 Service Worker
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      await cache.addAll(urlsToCache);
      await self.skipWaiting();
    })()
  );
});

// 激活 Service Worker
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const cacheNames = await caches.keys();
      await Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
          return Promise.resolve();
        })
      );
      await self.clients.claim();
    })()
  );
});

// 拦截请求并缓存
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET") {
    return;
  }

  if (!shouldCacheRequest(request, url)) {
    event.respondWith(fetch(request));
    return;
  }

  // 对核心静态资源使用网络优先，避免升级后仍拿到旧缓存。
  if (shouldUseNetworkFirst(request, url)) {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(request);
          if (fresh && fresh.status === 200) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, fresh.clone());
          }
          return fresh;
        } catch (_error) {
          const cached = await caches.match(request);
          if (cached) return cached;
          throw _error;
        }
      })()
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((response) => {
      if (response) {
        return response;
      }
      return fetch(request).then((response) => {
        if (!response || response.status !== 200 || response.type !== "basic") {
          return response;
        }

        const responseToCache = response.clone();

        caches.open(CACHE_NAME).then((cache) => {
          cache.put(request, responseToCache);
        });

        return response;
      });
    })
  );
});

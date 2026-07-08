// Service Worker для PWA «Сканер визиток».
// Стратегия: оболочка приложения кэшируется при установке,
// API и динамика всегда идут в сеть, остальные GET — network-first с фолбэком на кэш.

// Имя текущего кэша. При обновлении ассетов меняем версию (v1 -> v2 и т.д.).
const CACHE = "cardscan-v1";

// Обязательная оболочка приложения (app shell) — кэшируем сразу при установке.
// Если хоть один из этих URL не отдастся, install провалится — поэтому держим
// здесь только гарантированно существующее.
const APP_SHELL = [
  "/",
  "/static/app.js",
  "/manifest.webmanifest",
];

// Необязательные ассеты (иконки): кэшируем «как получится», по одному,
// чтобы их отсутствие не ломало регистрацию воркера.
const OPTIONAL_ASSETS = [
  "/static/icon-192.png",
  "/static/icon-512.png",
];

// Признаки запросов к API и динамическому контенту — их НИКОГДА не кэшируем.
const DYNAMIC_PATHS = [
  "/upload",
  "/jobs",
  "/qr.png",
  "/api/",
  "/settings",
  "/photos/",
];

// install: открываем кэш, складываем туда оболочку приложения
// и сразу активируем нового воркера (skipWaiting), не дожидаясь закрытия вкладок.
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => {
      // Необязательные ассеты — каждый отдельно, ошибки игнорируем.
      OPTIONAL_ASSETS.forEach((url) => {
        cache.add(url).catch(() => {});
      });
      // Обязательная оболочка — атомарно.
      return cache.addAll(APP_SHELL);
    })
  );
  self.skipWaiting();
});

// activate: удаляем все устаревшие кэши (имя != текущему CACHE)
// и берём управление уже открытыми клиентами (clients.claim).
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

// Проверка: относится ли запрос к API/динамике (его не кэшируем).
function isDynamic(url) {
  return DYNAMIC_PATHS.some((path) => url.pathname.startsWith(path));
}

// fetch: перехватываем ТОЛЬКО GET-запросы.
// POST и прочие методы (например, /upload через POST) не трогаем —
// браузер обрабатывает их сам, воркер не вмешивается.
self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Не GET — не перехватываем вовсе.
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);

  // API и динамика — всегда сеть, без кэширования.
  if (isDynamic(url)) {
    event.respondWith(fetch(request));
    return;
  }

  // Остальные GET — network-first с фолбэком на кэш.
  event.respondWith(
    fetch(request)
      .then((response) => {
        // Успешный ответ из сети кладём в кэш (копию, т.к. тело читается один раз).
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
        return response;
      })
      .catch(() =>
        // Сеть недоступна — пробуем отдать из кэша.
        caches.match(request).then((cached) => {
          if (cached) {
            return cached;
          }
          // Для навигаций при оффлайне отдаём корневую страницу из кэша.
          if (request.mode === "navigate") {
            return caches.match("/");
          }
          // Иначе вернём отказ — пусть браузер покажет ошибку сети.
          return Response.error();
        })
      )
  );
});

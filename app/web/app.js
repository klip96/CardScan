/*
 * Клиентская логика страницы съёмки визиток (vanilla JS, без фреймворков).
 *
 * Сценарий: сотрудник вводит источник (выставку), фотографирует визитки потоком.
 * Каждое фото мгновенно уходит на бэкенд (POST /upload) и встаёт в очередь;
 * камера тут же сбрасывается, чтобы снимать следующую визитку не дожидаясь
 * результата. Статусы обработки тянем поллингом (GET /jobs) и рисуем лентой.
 *
 * Контракт API:
 *   POST /upload  multipart/form-data: image=(файл), source_event=(строка)
 *                 -> { "job_id": "..." }
 *   GET  /jobs    -> { "jobs": [ { id, status, created_at, source_event,
 *                                  filename, result|null, results|null, error|null,
 *                                  sheet_rows|null, lead_statuses|null, lead_comments|null } ] }
 *   Статусы обработки: queued | recognizing | enriching | writing | done | failed
 *   lead_statuses/lead_comments — статус лида и комментарий из Google Sheets
 *   (заполняет офис-менеджер), выровнены по индексу с results. Пустая строка —
 *   решение по лиду ещё не принято.
 */
(function () {
  "use strict";

  // --- Настройки ---
  var POLL_INTERVAL_MS = 1500;          // период опроса /jobs
  var STORAGE_KEY = "cardscan.sourceEvent";
  var AUTH_KEY = "cardscan.auth";       // { token, user } — см. login.html

  // --- Ссылки на элементы DOM (id заданы в index.html) ---
  var sourceEventInput = document.getElementById("sourceEvent");
  var cameraInput = document.getElementById("cameraInput");
  var galleryInput = document.getElementById("galleryInput");
  var preview = document.getElementById("preview");
  var actionRow = document.getElementById("actionRow");
  var confirmBtn = document.getElementById("confirmBtn");
  var retakeBtn = document.getElementById("retakeBtn");
  var feed = document.getElementById("feed");
  var whoAmI = document.getElementById("whoAmI");
  var logoutBtn = document.getElementById("logoutBtn");

  // ----- Авторизация -----

  function getAuth() {
    try {
      var raw = window.localStorage.getItem(AUTH_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function clearAuth() {
    try { window.localStorage.removeItem(AUTH_KEY); } catch (e) { /* ignore */ }
  }

  function redirectToLogin() {
    window.location.href = "/login";
  }

  function authHeaders() {
    var auth = getAuth();
    return auth && auth.token ? { "Authorization": "Bearer " + auth.token } : {};
  }

  // Токен просрочен/отозван на сервере — разлогиниваем и уводим на /login.
  function handleUnauthorized() {
    clearAuth();
    redirectToLogin();
  }

  // --- Локальное состояние ---
  var selectedFile = null;       // выбранный, ещё не отправленный файл
  var previewUrl = null;         // object URL текущего превью (нужно освобождать)
  // Локальные превью по job_id: бэкенд не отдаёт URL снимка в /jobs,
  // поэтому миниатюру храним на клиенте с момента отправки.
  var localThumbs = {};          // { job_id: objectURL }
  var serverOnline = true;       // была ли последняя попытка опроса успешной

  // Человекочитаемые подписи статусов и класс для цвета (классы есть в CSS).
  var STATUS_LABELS = {
    queued: "В очереди",
    recognizing: "Распознаётся",
    enriching: "Обогащается",
    writing: "Запись в таблицу",
    done: "Готово",
    failed: "Ошибка"
  };
  var STATUS_ICONS = {
    queued: "⏳",
    recognizing: "🔍",
    enriching: "🌐",
    writing: "📝",
    done: "✅",
    failed: "⚠️"
  };
  // Бэкенд-статусы маппим на цветовые классы из index.html
  // (queued / uploading / processing / done / failed).
  var STATUS_CSS = {
    queued: "queued",
    recognizing: "processing",
    enriching: "processing",
    writing: "processing",
    done: "done",
    failed: "failed"
  };

  // Статус лида (колонка "Статус лида" в Google Sheets) — заполняется
  // офис-менеджером вручную, значения не фиксированы жёстко, поэтому
  // сравниваем без учёта регистра и красим только известные варианты.
  var LEAD_STATUS_META = {
    "взято в работу": { css: "lead-progress", icon: "🟠" },
    "наш пользователь": { css: "lead-won", icon: "🟢" },
    "решение конкурентов": { css: "lead-lost", icon: "🔴" }
  };

  // ----- Источник (сохраняется на устройстве) -----

  // При загрузке подставляем сохранённое значение источника.
  function restoreSourceEvent() {
    try {
      var saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved) {
        sourceEventInput.value = saved;
      }
    } catch (e) {
      // localStorage может быть недоступен (приватный режим) — не критично
    }
  }

  // При изменении сохраняем источник.
  function saveSourceEvent() {
    try {
      window.localStorage.setItem(STORAGE_KEY, sourceEventInput.value.trim());
    } catch (e) {
      // игнорируем недоступность хранилища
    }
  }

  // ----- Камера / превью -----

  // Сброс object URL, чтобы не течь памятью.
  function revokePreviewUrl() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
  }

  // Показ превью выбранного фото и кнопок confirm/retake.
  function showPreview(file) {
    revokePreviewUrl();
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    preview.classList.remove("hidden");
    actionRow.classList.remove("hidden");
  }

  // Полный сброс камеры: очистить выбор, превью и спрятать кнопки.
  function resetCamera() {
    selectedFile = null;
    revokePreviewUrl();
    preview.removeAttribute("src");
    preview.classList.add("hidden");
    actionRow.classList.add("hidden");
    // сбрасываем сами input'ы, иначе повторный выбор того же файла не даст change
    cameraInput.value = "";
    if (galleryInput) {
      galleryInput.value = "";
    }
  }

  // Общая обработка выбранного файла (с камеры ИЛИ из галереи).
  function handlePickedFile(file) {
    if (!file) {
      return;
    }
    selectedFile = file;
    showPreview(file);
  }

  // Съёмка камерой.
  function onCameraChange() {
    handlePickedFile(cameraInput.files && cameraInput.files[0]);
  }

  // Выбор готового фото из галереи/файлов.
  function onGalleryChange() {
    handlePickedFile(galleryInput.files && galleryInput.files[0]);
  }

  // ----- Отправка на бэкенд -----

  function onConfirm() {
    if (!selectedFile) {
      return;
    }

    var source = sourceEventInput.value.trim();
    if (!source) {
      // Мягкое предупреждение: без источника лид сложно атрибутировать.
      var proceed = window.confirm(
        "Источник (выставка) не указан. Отправить визитку без источника?"
      );
      if (!proceed) {
        sourceEventInput.focus();
        return;
      }
    }

    var formData = new FormData();
    formData.append("image", selectedFile, selectedFile.name || "card.jpg");
    formData.append("source_event", source);

    // Запоминаем превью отправляемого файла, чтобы показать его в ленте,
    // как только получим job_id (сам файл после сброса камеры будет недоступен).
    var thumbUrl = URL.createObjectURL(selectedFile);

    // Блокируем кнопку на время запроса.
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Отправка…";

    fetch("/upload", { method: "POST", headers: authHeaders(), body: formData })
      .then(function (resp) {
        if (resp.status === 401) {
          handleUnauthorized();
          throw new Error("Сессия истекла");
        }
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status);
        }
        return resp.json();
      })
      .then(function (data) {
        var jobId = data && data.job_id;
        if (jobId) {
          localThumbs[jobId] = thumbUrl;
        } else {
          URL.revokeObjectURL(thumbUrl);
        }
        // СРАЗУ сбрасываем камеру — можно снимать следующую визитку.
        resetCamera();
        // Обновляем ленту, не дожидаясь следующего тика поллинга.
        pollJobs();
      })
      .catch(function (err) {
        URL.revokeObjectURL(thumbUrl);
        window.alert("Не удалось отправить фото: " + err.message);
      })
      .then(function () {
        // finally: возвращаем кнопку в рабочее состояние
        confirmBtn.disabled = false;
        confirmBtn.textContent = "✅ Ок, в работу";
      });
  }

  function onRetake() {
    resetCamera();
  }

  // ----- Лента обработки (поллинг /jobs) -----

  // Безопасный текст: используем textContent, чтобы не вставлять HTML.
  function setText(el, text) {
    el.textContent = text == null ? "" : String(text);
  }

  // Строит заголовок карточки из результата распознавания.
  function buildTitle(job) {
    if (job.result) {
      var name = (job.result.name || "").trim();
      var company = (job.result.company || "").trim();
      if (name && company) {
        return name + " — " + company;
      }
      if (name) {
        return name;
      }
      if (company) {
        return company;
      }
    }
    if (job.status === "failed") {
      return "Не распознано";
    }
    return "Визитка";
  }

  // Подпись под заголовком: источник, телефоны/почта или текст ошибки.
  function buildMeta(job) {
    if (job.status === "failed" && job.error) {
      return "Ошибка: " + job.error;
    }
    var parts = [];
    if (job.source_event) {
      parts.push(job.source_event);
    }
    if (job.result) {
      var phones = job.result.phones;
      if (phones && phones.length) {
        parts.push(phones[0]);
      }
      var emails = job.result.emails;
      if (emails && emails.length) {
        parts.push(emails[0]);
      }
    }
    return parts.join(" · ");
  }

  // Список визиток джоба (на одном фото может быть несколько).
  function jobCards(job) {
    if (job.results && job.results.length) {
      return job.results;
    }
    return job.result ? [job.result] : [];
  }

  // Статус лида для карточки с индексом i (выровнен с results/lead_statuses).
  function leadStatusFor(job, i) {
    var arr = job.lead_statuses;
    if (!arr || i >= arr.length) { return ""; }
    return (arr[i] || "").trim();
  }

  function leadCommentFor(job, i) {
    var arr = job.lead_comments;
    if (!arr || i >= arr.length) { return ""; }
    return (arr[i] || "").trim();
  }

  function leadStatusMeta(status) {
    return LEAD_STATUS_META[status.toLowerCase()] || { css: "lead-other", icon: "🔵" };
  }

  // Бейдж статуса лида: <span class="lead-badge lead-...">🟢 текст</span>.
  // title — комментарий менеджера (если есть), виден по долгому тапу/наведению.
  function buildLeadBadge(status, comment) {
    var meta = leadStatusMeta(status);
    var span = document.createElement("span");
    span.className = "lead-badge " + meta.css;
    setText(span, meta.icon + " " + status);
    if (comment) {
      span.title = comment;
    }
    return span;
  }

  // Одна строка визитки: "Имя — Компания · телефон · email".
  function cardLine(card) {
    var name = (card.name || "").trim();
    var company = (card.company || "").trim();
    var t = name && company ? name + " — " + company : (name || company || "Визитка");
    var extra = [];
    if (card.phones && card.phones.length) { extra.push(card.phones[0]); }
    if (card.emails && card.emails.length) { extra.push(card.emails[0]); }
    return extra.length ? t + " · " + extra.join(" · ") : t;
  }

  // Склонение слова «визитка» по числу.
  function pluralCards(n) {
    var n10 = n % 10, n100 = n % 100;
    if (n10 === 1 && n100 !== 11) { return "визитка"; }
    if (n10 >= 2 && n10 <= 4 && (n100 < 10 || n100 >= 20)) { return "визитки"; }
    return "визиток";
  }

  // Создаёт DOM-элемент одной карточки джоба.
  function renderJob(job) {
    var li = document.createElement("li");
    li.className = "job";

    // Миниатюра — только если есть локально сохранённое превью.
    var thumbUrl = localThumbs[job.id];
    if (thumbUrl) {
      var img = document.createElement("img");
      img.className = "job-thumb";
      img.src = thumbUrl;
      img.alt = "";
      li.appendChild(img);
    }

    var body = document.createElement("div");
    body.className = "job-body";

    var cards = jobCards(job);
    var multi = cards.length > 1;

    var title = document.createElement("div");
    title.className = "job-title";
    if (multi) {
      setText(title, "🪪 " + cards.length + " " + pluralCards(cards.length));
    } else {
      setText(title, buildTitle(job));
    }
    body.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "job-meta";
    setText(meta, multi ? (job.source_event || "") : buildMeta(job));
    body.appendChild(meta);

    // Если визиток несколько — перечислим каждую отдельной строкой,
    // с бейджем статуса лида, если он уже проставлен в таблице.
    if (multi) {
      var ul = document.createElement("ul");
      ul.className = "job-cards";
      for (var k = 0; k < cards.length; k++) {
        var liCard = document.createElement("li");
        var textSpan = document.createElement("span");
        setText(textSpan, cardLine(cards[k]));
        liCard.appendChild(textSpan);
        var statusMulti = leadStatusFor(job, k);
        if (statusMulti) {
          liCard.appendChild(buildLeadBadge(statusMulti, leadCommentFor(job, k)));
        }
        ul.appendChild(liCard);
      }
      body.appendChild(ul);
    } else {
      // Одна визитка на фото — бейдж статуса отдельной строкой под meta.
      var statusSingle = leadStatusFor(job, 0);
      if (statusSingle) {
        var leadRow = document.createElement("div");
        leadRow.className = "job-lead-row";
        leadRow.appendChild(buildLeadBadge(statusSingle, leadCommentFor(job, 0)));
        body.appendChild(leadRow);
      }
    }

    li.appendChild(body);

    var status = document.createElement("span");
    var cssClass = STATUS_CSS[job.status] || "queued";
    status.className = "job-status " + cssClass;
    var icon = STATUS_ICONS[job.status] || "";
    var label = STATUS_LABELS[job.status] || job.status;
    setText(status, icon ? icon + " " + label : label);
    li.appendChild(status);

    return li;
  }

  // Отрисовка всей ленты. Бэкенд уже отдаёт свежие сверху.
  function renderFeed(jobs) {
    feed.innerHTML = "";
    for (var i = 0; i < jobs.length; i++) {
      feed.appendChild(renderJob(jobs[i]));
    }
  }

  // Показ состояния «нет связи с сервером» вместо ленты.
  function renderOffline() {
    feed.innerHTML = "";
    var li = document.createElement("li");
    li.className = "job";
    var body = document.createElement("div");
    body.className = "job-body";
    var title = document.createElement("div");
    title.className = "job-title";
    setText(title, "Нет связи с сервером");
    body.appendChild(title);
    var meta = document.createElement("div");
    meta.className = "job-meta";
    setText(meta, "Пытаемся переподключиться…");
    body.appendChild(meta);
    li.appendChild(body);
    var status = document.createElement("span");
    status.className = "job-status failed";
    setText(status, "⚠️ Оффлайн");
    li.appendChild(status);
    feed.appendChild(li);
  }

  // Один тик поллинга.
  function pollJobs() {
    return fetch("/jobs", { method: "GET", cache: "no-store", headers: authHeaders() })
      .then(function (resp) {
        if (resp.status === 401) {
          handleUnauthorized();
          throw new Error("Сессия истекла");
        }
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status);
        }
        return resp.json();
      })
      .then(function (data) {
        serverOnline = true;
        var jobs = (data && data.jobs) || [];
        renderFeed(jobs);
      })
      .catch(function () {
        // Сеть/сервер недоступны — не падаем, показываем статус.
        if (serverOnline) {
          serverOnline = false;
          renderOffline();
        }
      });
  }

  // Шапка: показываем, кто вошёл, и кнопку выхода.
  function renderWhoAmI() {
    var auth = getAuth();
    if (!whoAmI || !auth || !auth.user) { return; }
    var label = auth.user.username;
    if (auth.user.position) { label += " · " + auth.user.position; }
    setText(whoAmI, label);
  }

  function onLogout() {
    var auth = getAuth();
    var headers = authHeaders();
    clearAuth();
    // Отзываем сессию на сервере, но не ждём ответа — уходим на /login сразу.
    if (auth) {
      fetch("/api/auth/logout", { method: "POST", headers: headers }).catch(function () {});
    }
    redirectToLogin();
  }

  // ----- Инициализация -----

  function init() {
    // Нет валидного токена на устройстве — сразу на экран входа,
    // без вспышки содержимого камеры/ленты.
    if (!getAuth()) {
      redirectToLogin();
      return;
    }

    renderWhoAmI();
    if (logoutBtn) {
      logoutBtn.addEventListener("click", onLogout);
    }

    restoreSourceEvent();

    sourceEventInput.addEventListener("input", saveSourceEvent);
    sourceEventInput.addEventListener("change", saveSourceEvent);
    cameraInput.addEventListener("change", onCameraChange);
    if (galleryInput) {
      galleryInput.addEventListener("change", onGalleryChange);
    }
    confirmBtn.addEventListener("click", onConfirm);
    retakeBtn.addEventListener("click", onRetake);

    registerServiceWorker();

    // Первый опрос сразу, затем по таймеру.
    pollJobs();
    window.setInterval(pollJobs, POLL_INTERVAL_MS);
  }

  // Регистрация service worker — чтобы страницу можно было установить как
  // приложение (PWA) на телефон. Ошибки не критичны.
  function registerServiceWorker() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(function (e) {
        // SW не обязателен для работы — просто логируем
        if (window.console) {
          window.console.warn("Service worker не зарегистрирован:", e);
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

window.addEventListener("DOMContentLoaded", async () => {
    const API_BASE_URL = "/api";

    /** Заголовки для ендпоінтів з JWT (сесії тощо). Після логіну токен у localStorage. */
    function authHeaders(base = {}) {
        const token = localStorage.getItem("access_token");
        const h = { ...base };
        if (token) {
            h.Authorization = `Bearer ${token}`;
        }
        return h;
    }

    /** Показувати блок «посилання» лише для реального http(s) URL. */
    function isHttpUrl(s) {
        const t = (s || "").trim();
        if (!t) return false;
        const bad = ["немає", "none", "n/a", "null", "-"];
        if (bad.includes(t.toLowerCase())) return false;
        try {
            const u = new URL(t);
            return u.protocol === "http:" || u.protocol === "https:";
        } catch {
            return false;
        }
    }

    /** Чи схожий фрагмент на LaTeX (щоб не чіпати звичайні квадратні дужки). Потрібен хоча б один \\команда. */
    function looksLikeLatexFragment(t) {
        const s = (t || "").trim();
        if (!s || s.length > 800) return false;
        return /\\[a-zA-Z]+/.test(s);
    }

    /**
     * Моделі часто дають блоки виду "[\\n I=\\\\frac{q}{t} \\n]" без зворотного сліша —
     * KaTeX чекає $$ або \\[. Перетворюємо такі блоки на $$...$$.
     */
    function normalizeMathMarkdown(text) {
        let s = text == null ? "" : String(text);
        s = s.replace(/(^|\n)\[\s*\n([\s\S]*?)\n\]\s*(?=\n|$)/g, (full, before, inner) => {
            const t = inner.trim();
            if (looksLikeLatexFragment(t)) {
                const one = t.replace(/\s+/g, " ").trim();
                return `${before}$$${one}$$`;
            }
            return full;
        });
        /* Один рядок: і після початку рядка, і посеред речення (напр. «часу: [ I = \\frac{q}{t} ]»). */
        s = s.replace(/(?<!\!)\[\s*([^\]\n]+?)\s*\]/g, (full, inner) => {
            const t = inner.trim();
            if (!looksLikeLatexFragment(t)) return full;
            const one = t.replace(/\s+/g, " ").trim();
            return `$$${one}$$`;
        });
        /*
         * marked + breaks:true перетворює переноси всередині абзацу на <br>.
         * Тоді $$ … $$ опиняються в різних текстових вузлах з <br> між ними — KaTeX auto-render не знаходить пару delimiter'ів.
         * Якщо немає LaTeX-розриву рядка (\\\\) — зливаємо вміст $$ у один рядок.
         */
        s = s.replace(/\$\$([\s\S]*?)\$\$/g, (full, inner) => {
            const core = inner.trim();
            if (!core) return full;
            if (/\\\\/.test(core)) {
                return `$$${core}$$`;
            }
            return `$$${core.replace(/\s+/g, " ").trim()}$$`;
        });
        return s;
    }

    /** Markdown → безпечний HTML (якщо CDN недоступні — екранування). */
    function renderMarkdownToHtml(md) {
        const raw = normalizeMathMarkdown(md == null ? "" : String(md));
        if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
            const d = document.createElement("div");
            d.textContent = raw;
            return d.innerHTML.replace(/\n/g, "<br>");
        }
        const html = marked.parse(raw, { breaks: true, gfm: true });
        let clean = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
        clean = clean.replace(/<p>\s*<\/p>/gi, "");
        clean = clean.replace(/<p>\s*<br\s*\/?>\s*<\/p>/gi, "");
        const wrap = document.createElement("div");
        wrap.innerHTML = clean;
        wrap.querySelectorAll("a[href]").forEach((a) => {
            a.setAttribute("target", "_blank");
            a.setAttribute("rel", "noopener noreferrer");
        });
        return wrap.innerHTML;
    }

    let streamMathTimer = null;

    function flushStreamingMathTimer() {
        if (streamMathTimer != null) {
            clearTimeout(streamMathTimer);
            streamMathTimer = null;
        }
    }

    /** LaTeX у Markdown: \\(…\\) у рядку, $$…$$ або \\[…\\] блочно (одинарний $ не використовуємо — зламає гривні тощо). */
    function enhanceMath(root) {
        if (!root || !root.isConnected) return;
        if (typeof renderMathInElement !== "function" || typeof katex === "undefined") {
            return;
        }
        try {
            renderMathInElement(root, {
                delimiters: [
                    { left: "$$", right: "$$", display: true },
                    { left: "\\(", right: "\\)", display: false },
                    { left: "\\[", right: "\\]", display: true },
                ],
                ignoredClasses: ["katex", "katex-display", "katex-html"],
                throwOnError: false,
            });
        } catch (err) {
            console.warn("KaTeX", err);
        }
    }

    function setMarkdownHtml(el, md) {
        flushStreamingMathTimer();
        el.innerHTML = renderMarkdownToHtml(md);
        enhanceMath(el);
    }

    function setStreamingMarkdownHtml(el, md) {
        el.innerHTML = renderMarkdownToHtml(md);
        flushStreamingMathTimer();
        streamMathTimer = setTimeout(() => {
            streamMathTimer = null;
            enhanceMath(el);
        }, 450);
    }

    /** Під час стріму показуємо лише тіло після «основний текст:», без службових полів. */
    function extractMainStreamPreview(full) {
        const raw = full == null ? "" : String(full);
        const m = raw.match(/основний\s*текст\s*:\s*(.*)/is);
        if (!m) return null;
        let body = m[1];
        body = body.split(/\n\s*емоція\s*:/i)[0];
        body = body.split(/\n\s*посилання\s*:/i)[0];
        body = body.split(/\n\s*текст\s*чату\s*:/i)[0];
        return body;
    }

    async function consumeSseStream(response, onEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let sep;
            while ((sep = buffer.indexOf("\n\n")) !== -1) {
                const block = buffer.slice(0, sep).trim();
                buffer = buffer.slice(sep + 2);
                for (const line of block.split("\n")) {
                    const t = line.trim();
                    if (!t.startsWith("data:")) continue;
                    const jsonStr = t.slice(5).trim();
                    if (jsonStr === "[DONE]") continue;
                    try {
                        onEvent(JSON.parse(jsonStr));
                    } catch (err) {
                        console.warn("SSE parse", err, jsonStr);
                    }
                }
            }
        }
    }

    // Елементи DOM
    const sidebar = document.getElementById("sidebar");
    const mainContent = document.getElementById("main-content");
    const toggleBtn = document.getElementById("sidebar-toggle");
    const messagesContainer = document.getElementById("messages");
    const userInput = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");
    const attachBtn = document.getElementById("attach-btn");
    const voiceBtn = document.getElementById("voice-btn");
    const fileInput = document.getElementById("file-input");
    let pendingFile = null;
    const pendingFileBar = document.getElementById("pending-file-bar");
    const pendingFileNameEl = document.getElementById("pending-file-name");
    const pendingFileRemoveBtn = document.getElementById("pending-file-remove");
    let chatInFlight = false;
    /** Скасування поточного SSE-запиту (кнопка «Зупинити»). */
    let streamAbortController = null;

    const SEND_BTN_ICON_HTML = '<i class="bi bi-send-fill"></i>';
    const STOP_BTN_ICON_HTML = '<i class="bi bi-stop-fill"></i>';

    function setSendButtonMode(mode) {
        if (!sendBtn) return;
        if (mode === "stop") {
            sendBtn.classList.add("btn-send--stop");
            sendBtn.innerHTML = STOP_BTN_ICON_HTML;
            sendBtn.title = "Зупинити відповідь";
            sendBtn.setAttribute("aria-label", "Зупинити генерацію відповіді");
        } else {
            sendBtn.classList.remove("btn-send--stop");
            sendBtn.innerHTML = SEND_BTN_ICON_HTML;
            sendBtn.title = "Надіслати";
            sendBtn.setAttribute("aria-label", "Надіслати повідомлення");
        }
    }
    const avatarVideoStack = document.getElementById("avatar-video-stack");
    const avatarLayerA = document.getElementById("avatar-video-a");
    const avatarLayerB = document.getElementById("avatar-video-b");
    /** Активний (видимий) плеєр після двошарового перемикання. */
    let _activeAvatarLayer =
        avatarLayerA && avatarLayerB ? avatarLayerA : null;

    /** Покоління перемикань — щоб повільний load не затер новіший запит. */
    let _avatarSwapGen = 0;

    const emotionLabel = document.getElementById("emotion-status");

    // ── Emotion Engine: мап емоцій → emoji + назва (синхронізовано з emotion_engine.py) ──
    const EMOTION_META = {
        happy:    { emoji: "😊", label: "радісно",    color: "#f5a623" },
        sad:      { emoji: "😔", label: "сумно",      color: "#7b9dc8" },
        surprise: { emoji: "😲", label: "здивовано",  color: "#a855f7" },
        thinking: { emoji: "🤔", label: "задумливо", color: "#10b981" },
        neutral:  { emoji: "😐", label: "нейтрально", color: "#6b7280" },
        angry:    { emoji: "😠", label: "злісно",     color: "#ef4444" },
        disgust:  { emoji: "🤢", label: "огида",      color: "#84cc16" },
    };

    // Порог впевненості для показу бейджу на повідомленні
    const EMOTION_BADGE_THRESHOLD = 0.40;

    /** Той самий поріг для аватару за емоцією користувача (інакше бейдж є, а кліп лишається старим). */
    const USER_EMOTION_AVATAR_THRESHOLD = EMOTION_BADGE_THRESHOLD;

    /**
     * Оновлює лейбл "Стан" під аватаром.
     * Якщо передано `scores` — показує топ-2 емоції з відсотками та кольором.
     * Якщо scores відсутні — показує одну емоцію з кольором і впевненістю.
     *
     * @param {string} primaryEmotion  — основна емоція (ключ з EMOTION_META)
     * @param {Object|null} scores     — розподіл {emotion: probability, ...} або null
     * @param {number|null} confidence — впевненість [0..1] або null
     */
    function renderEmotionLabel(primaryEmotion, scores, confidence) {
        if (!emotionLabel) return;
        emotionLabel.dataset.state = "active";

        // Топ-1 емоція з відсотком (один компактний рядок)
        const top = scores && typeof scores === "object"
            ? Object.entries(scores).sort(([, a], [, b]) => b - a)[0]
            : null;
        const emoKey = top ? top[0] : primaryEmotion;
        const m = EMOTION_META[emoKey] || EMOTION_META.neutral;
        const pct = top
            ? Math.round(top[1] * 100)
            : (confidence != null ? Math.round(confidence * 100) : null);
        const pctStr = pct != null ? `&nbsp;<small style="opacity:.65;font-variant-numeric:tabular-nums">${pct}%</small>` : "";
        emotionLabel.innerHTML =
            `<span style="color:${m.color};font-weight:600">${m.emoji}&nbsp;${m.label}${pctStr}</span>`;
    }

    /** Останній .mp4; ініціалізуємо з DOM, щоб перший перехід не «зіпсувати» порівняння. */
    function initialChatAvatarFilename() {
        const el =
            (_activeAvatarLayer && _activeAvatarLayer.isConnected
                ? _activeAvatarLayer
                : avatarLayerA) || null;
        if (!el) return "";
        const fromUrl = (u) => {
            if (!u) return "";
            try {
                const path = decodeURIComponent(new URL(u, window.location.href).pathname);
                const seg = path.split("/").filter(Boolean).pop();
                return seg || "";
            } catch (_) {
                return "";
            }
        };
        let name = fromUrl(el.currentSrc) || fromUrl(el.src);
        return name || "muse.mp4";
    }

    let _lastAvatarFilename = initialChatAvatarFilename();

    /**
     * Перший з loadeddata / canplay (кеш браузера інколи пропускає один із них).
     */
    function whenClipReady(videoEl, genSnapshot, then) {
        let finished = false;
        const run = () => {
            videoEl.removeEventListener("loadeddata", run);
            videoEl.removeEventListener("canplay", run);
            if (finished || genSnapshot !== _avatarSwapGen) return;
            finished = true;
            then();
        };
        videoEl.addEventListener("loadeddata", run);
        videoEl.addEventListener("canplay", run);
    }

    /**
     * Два <video>: новий кліп декодується на прихованому шарі, потім лише клас is-active (+ opacity).
     * Сторінка / картка не «перезавантажуються»; немає глухого кадру одного плеєра перед play.
     * @param {Object} [opts]
     * @param {boolean} [opts.fade=true] — короткий crossfade між шарами; false — без transition для ротації.
     * @param {boolean} [opts.playThroughOnce=false] — один повний прохід без loop (для ротації по «ended»).
     * @param {function} [opts.onEnded] — після playThroughOnce, коли кліп відіграв до кінця.
     */
    function applyAvatarTransition(emotion, filename, opts = {}) {
        const fade = opts.fade !== false;
        const playThroughOnce = opts.playThroughOnce === true;
        const onEnded = opts.onEnded;
        if (
            !avatarVideoStack ||
            !avatarLayerA ||
            !avatarLayerB ||
            !_activeAvatarLayer ||
            !filename
        ) {
            return;
        }

        const active = _activeAvatarLayer;
        const idle = active === avatarLayerA ? avatarLayerB : avatarLayerA;

        if (_lastAvatarFilename === filename && !playThroughOnce) {
            try {
                active.loop = true;
                active.currentTime = 0;
                active.play().catch(() => {});
            } catch (_) {}
            return;
        }

        const path = `/avatar/animations/${filename}`;
        const gen = ++_avatarSwapGen;

        const finalize = () => {
            if (gen !== _avatarSwapGen) return;
            idle.currentTime = 0;
            active.classList.remove("is-active");
            active.pause();
            idle.classList.add("is-active");
            idle.loop = !playThroughOnce;
            idle.play().catch(() => {});
            if (playThroughOnce && typeof onEnded === "function") {
                const once = () => {
                    idle.removeEventListener("ended", once);
                    if (gen !== _avatarSwapGen) return;
                    onEnded();
                };
                idle.addEventListener("ended", once);
            }
            _activeAvatarLayer = idle;
            _lastAvatarFilename = filename;
        };

        if (!fade) {
            whenClipReady(idle, gen, () => {
                if (gen !== _avatarSwapGen) return;
                avatarVideoStack.classList.add("avatar-swap-instant");
                finalize();
                requestAnimationFrame(() => {
                    requestAnimationFrame(() =>
                        avatarVideoStack.classList.remove("avatar-swap-instant")
                    );
                });
            });
            idle.src = path;
            idle.load();
            return;
        }

        whenClipReady(idle, gen, finalize);
        idle.src = path;
        idle.load();
    }

    /**
     * Відображає бейдж емоції над повідомленням користувача.
     * Показується лише якщо emotion != neutral і confidence > порогу.
     * Дані приходять від EmotionEngine (Python backend).
     */
    function createUserEmotionBadge(emotionData) {
        if (!emotionData) return null;
        const { emotion, confidence } = emotionData;
        if (emotion === "neutral" || confidence < EMOTION_BADGE_THRESHOLD) return null;
        const meta = EMOTION_META[emotion] || EMOTION_META.neutral;
        const badge = document.createElement("div");
        badge.className = "user-emotion-badge";
        badge.setAttribute("title", `EmotionEngine: ${emotion} (впевненість ${Math.round(confidence * 100)}%)`);
        badge.style.cssText = [
            "display:inline-flex", "align-items:center", "gap:4px",
            "font-size:11px", `color:${meta.color}`,
            "opacity:0.75", "margin-bottom:2px",
            "font-weight:500", "letter-spacing:0.02em",
            "cursor:default",
        ].join(";");
        badge.innerHTML = `<span>${meta.emoji}</span><span>${meta.label}</span>`;
        return badge;
    }
    const newChatBtn = document.getElementById("new-chat-btn");
    const avatarBox = document.querySelector('.avatar-fixed');
    const resizeHandle = document.querySelector('.resize-handle');

    /** Дефолтна позиція аватара: над блоком вводу (після drag залишаються top/left — не чіпаємо). */
    function syncAvatarDefaultBottom() {
        if (!avatarBox) return;
        if (avatarBox.style.top) return;
        const inputBar = document.querySelector(".input-area");
        if (!inputBar) return;
        const gap = 20;
        const h = inputBar.getBoundingClientRect().height;
        avatarBox.style.bottom = `${Math.ceil(h + gap)}px`;
    }
    requestAnimationFrame(() => syncAvatarDefaultBottom());
    window.addEventListener("resize", () => requestAnimationFrame(syncAvatarDefaultBottom));
    const confirmLogout = document.getElementById("confirmLogout");

    /**
     * Основний кліп на клас (high confidence) — узгоджено з AvatarController у emotion_engine.py.
     * Усі 12 файлів з avatar/animations/ використовуються на бекенді через high/med/fallback.
     */
    const AVATAR_VIDEO_MAP = {
        neutral:  "muse.mp4",
        happy:    "excited.mp4",
        sad:      "sad.mp4",
        surprise: "surprize1.mp4",
        thinking: "squinted1.mp4",
        angry:    "angry.mp4",
        disgust:  "disgust.mp4",
        // emoji fallback (старий формат парсингу)
        "😊": "excited.mp4",
        "😄": "excited.mp4",
        "😲": "surprize1.mp4",
        "🤔": "squinted1.mp4",
        "😍": "excited.mp4",
        "😠": "angry.mp4",
        "🤢": "disgust.mp4",
    };

    /** Пули з 3 кліпів (high/med/fallback) — як у AvatarController в emotion_engine.py; цикл під час очікування відповіді */
    const AVATAR_ROTATION_POOLS = {
        neutral:  ["muse.mp4", "speak_blink.mp4", "speak.mp4"],
        happy:    ["excited.mp4", "happy.mp4", "happy.mp4"],
        sad:      ["sad.mp4", "speak.mp4", "muse.mp4"],
        surprise: ["surprize1.mp4", "fear.mp4", "confused.mp4"],
        thinking: ["squinted1.mp4", "confused.mp4", "speak_blink.mp4"],
        angry:    ["angry.mp4", "speak.mp4", "fear.mp4"],
        disgust:  ["disgust.mp4", "squinted1.mp4", "muse.mp4"],
    };

    /** Мін. пауза перед «задумливою» ротацією — якщо одразу прийде user_emotion, не показуємо два стани поспіль. */
    const THINKING_AVATAR_DEBOUNCE_MS = 480;

    /** Покоління ротації: скидається в clearAvatarRotation, щоб старі onEnded не спрацьовували. */
    let avatarRotationGeneration = 0;

    let thinkingAvatarDebounceId = null;

    function cancelThinkingAvatarDebounce() {
        if (thinkingAvatarDebounceId != null) {
            clearTimeout(thinkingAvatarDebounceId);
            thinkingAvatarDebounceId = null;
        }
    }

    /**
     * Остання виразна емоція користувача за поточне відправлене повідомлення (поки LLM відповідає).
     * Після `done` промпт часто дає neutral — не скидаємо аватар у muse, доки не нове повідомлення.
     */
    let stickyUserAvatarThisTurn = null;

    function clearAvatarRotation() {
        cancelThinkingAvatarDebounce();
        avatarRotationGeneration++;
    }

    /** Скільки різних кліпів у циклі ротації: частіше 1–2, інколи 3. Для thinking — ще більше «одна форма». */
    function pickRotationPlaylistLength(emotionKey, poolLen) {
        const r = Math.random();
        if (emotionKey === "thinking") {
            if (r < 0.58) return 1;
            if (r < 0.92) return Math.min(2, poolLen);
            return Math.min(3, poolLen);
        }
        if (r < 0.42) return 1;
        if (r < 0.78) return Math.min(2, poolLen);
        return Math.min(3, poolLen);
    }

    function buildRotationPlaylist(emotionKey, hintFilename) {
        const key =
            AVATAR_ROTATION_POOLS[emotionKey] !== undefined
                ? emotionKey
                : "neutral";
        const full = AVATAR_ROTATION_POOLS[key];
        if (!full || full.length === 0) return { key, playlist: [] };
        const n = pickRotationPlaylistLength(key, full.length);
        let start = 0;
        if (
            hintFilename &&
            typeof hintFilename === "string" &&
            full.includes(hintFilename)
        ) {
            start = full.indexOf(hintFilename);
        }
        const playlist = [];
        for (let i = 0; i < n; i++) {
            playlist.push(full[(start + i) % full.length]);
        }
        return { key, playlist };
    }

    /**
     * Будує змішаний плейліст з топ-2 емоцій на основі розподілу scores.
     * Основна емоція отримує 2 кліпи, вторинна (якщо score > 0.14) — 1 кліп.
     * Повертає { primaryKey, playlist }.
     *
     * @param {Object} scores       — розподіл ймовірностей {emotion: float}
     * @param {string|null} hintFilename — кліп від бекенду (пріоритет у основній емоції)
     */
    function buildMultiEmotionPlaylist(scores, hintFilename) {
        const SECONDARY_THRESHOLD = 0.14;

        // Топ-2 емоції з пулами
        const candidates = Object.entries(scores || {})
            .filter(([emo, v]) => v > SECONDARY_THRESHOLD && AVATAR_ROTATION_POOLS[emo])
            .sort(([, a], [, b]) => b - a)
            .slice(0, 2);

        if (candidates.length === 0) {
            return { primaryKey: "neutral", playlist: ["muse.mp4"] };
        }

        const [[primaryKey, primaryScore], secondary] = [candidates[0], candidates[1]];
        const primaryPool = AVATAR_ROTATION_POOLS[primaryKey] || ["muse.mp4"];

        // Визначаємо старт у основному пулі (підказка від бекенду)
        let start = 0;
        if (hintFilename && primaryPool.includes(hintFilename)) {
            start = primaryPool.indexOf(hintFilename);
        }

        // Кількість кліпів основної емоції: 1 якщо домінує і є вторинна, інакше 2
        const primaryCount = secondary && primaryScore < 0.72 ? 2 : Math.min(2, primaryPool.length);
        const playlist = [];
        for (let i = 0; i < primaryCount; i++) {
            playlist.push(primaryPool[(start + i) % primaryPool.length]);
        }

        // Додаємо 1 кліп вторинної емоції якщо вона достатньо значуща
        if (secondary) {
            const [secKey, secScore] = secondary;
            if (secScore > SECONDARY_THRESHOLD) {
                const secPool = AVATAR_ROTATION_POOLS[secKey] || [];
                if (secPool.length > 0) playlist.push(secPool[0]);
            }
        }

        return { primaryKey, playlist };
    }

    /**
     * Запускає цикл ротації кліпів змішаного плейліста (топ-2 емоції).
     * Використовується коли є повний розподіл scores від EmotionEngine.
     *
     * @param {Object} scores       — розподіл ймовірностей {emotion: float}
     * @param {string|null} hintFilename — кліп від бекенду
     */
    function startMultiEmotionRotation(scores, hintFilename = null) {
        if (!avatarLayerA || !avatarLayerB) return;
        clearAvatarRotation();
        const token = avatarRotationGeneration;
        const { primaryKey, playlist } = buildMultiEmotionPlaylist(scores, hintFilename);
        if (!playlist.length) return;

        let i = 0;
        function step() {
            if (token !== avatarRotationGeneration) return;
            const fn = playlist[i % playlist.length];
            // Емоція для кліпу: визначаємо з якого пулу він походить
            const clipKey = Object.entries(AVATAR_ROTATION_POOLS).find(
                ([, pool]) => pool.includes(fn)
            )?.[0] || primaryKey;
            applyAvatarTransition(clipKey, fn, {
                fade: false,
                playThroughOnce: true,
                onEnded: () => {
                    if (token !== avatarRotationGeneration) return;
                    i = (i + 1) % playlist.length;
                    step();
                },
            });
        }
        step();
    }

    /**
     * Під час генерації — цикл з 1–3 кліпів; кожен кліп відтворюється повністю (ended), не обрізається таймером.
     * hintFilename: якщо є (з бекенду), входить у плейлист із відповідного місця в пулі.
     */
    function startAvatarRotation(emotionKey, hintFilename = null) {
        if (!avatarLayerA || !avatarLayerB) return;
        clearAvatarRotation();
        const token = avatarRotationGeneration;
        const { key, playlist } = buildRotationPlaylist(emotionKey, hintFilename);
        if (!playlist.length) return;

        let i = 0;
        function step() {
            if (token !== avatarRotationGeneration) return;
            const fn = playlist[i % playlist.length];
            applyAvatarTransition(key, fn, {
                fade: false,
                playThroughOnce: true,
                onEnded: () => {
                    if (token !== avatarRotationGeneration) return;
                    i = (i + 1) % playlist.length;
                    step();
                },
            });
        }
        step();
    }

    function scheduleThinkingAvatarRotation() {
        cancelThinkingAvatarDebounce();
        thinkingAvatarDebounceId = setTimeout(() => {
            thinkingAvatarDebounceId = null;
            startAvatarRotation("thinking");
        }, THINKING_AVATAR_DEBOUNCE_MS);
    }

    /**
     * Застосовує емоцію до аватара.
     * Якщо event містить user_emotion (від EmotionEngine) — аватар реагує на емоцію КОРИСТУВАЧА.
     * Якщо є тільки emotion (від LLM) — аватар відображає стан АСИСТЕНТА.
     *
     * Пріоритет для кліпу аватара: user_emotion із done (confidence ≥ порогу) > «липка» емоція
     * цього відправлення (SSE user_emotion) > емоція асистента з промпта.
     * Лейбл «Стан»: якщо в done є user_emotion — завжди показуємо його scores/confidence (відсотки),
     * навіть коли для відео обрано емоцію асистента (поріг не застосовується до цифр у підписі).
     */
    function applyAvatarEmotion(data) {
        clearAvatarRotation();
        if (avatarBox) {
            avatarBox.classList.add("active-glow");
            setTimeout(() => avatarBox.classList.remove("active-glow"), 3000);
        }

        // Визначаємо, яку емоцію показати на аватарі
        let displayEmotion = (data.emotion || "neutral").trim().toLowerCase();

        const ue = data.user_emotion;
        const conf = (x) => Number(x) || 0;
        const threshold = USER_EMOTION_AVATAR_THRESHOLD - 1e-9;
        const ueStrong =
            ue &&
            ue.emotion &&
            ue.emotion !== "neutral" &&
            conf(ue.confidence) >= threshold;
        const sticky = stickyUserAvatarThisTurn;
        const stickyStrong =
            sticky &&
            sticky.emotion &&
            sticky.emotion !== "neutral" &&
            conf(sticky.confidence) >= threshold;

        let filename;
        let labelEmotion, labelScores, labelConf;

        if (ueStrong) {
            displayEmotion = ue.emotion;
            filename =
                ue.avatar_filename ||
                AVATAR_VIDEO_MAP[displayEmotion] ||
                "muse.mp4";
            labelEmotion = ue.emotion;
            labelScores   = ue.scores || null;
            labelConf     = conf(ue.confidence);
        } else if (stickyStrong) {
            displayEmotion = sticky.emotion;
            filename =
                sticky.avatar_filename ||
                AVATAR_VIDEO_MAP[displayEmotion] ||
                "muse.mp4";
            labelEmotion = sticky.emotion;
            labelScores   = sticky.scores || null;
            labelConf     = conf(sticky.confidence);
        } else {
            const rawKey = AVATAR_VIDEO_MAP[displayEmotion] ? displayEmotion : "neutral";
            displayEmotion = rawKey;
            filename =
                data.avatar_filename ||
                AVATAR_VIDEO_MAP[displayEmotion] ||
                "muse.mp4";
            labelEmotion = displayEmotion;
            labelScores   = null;   // від LLM scores немає
            labelConf     = null;
        }

        // Підпис під аватаром: завжди з цифрами з аналізу користувача, якщо done їх передав.
        if (ue && ue.emotion != null && String(ue.emotion).trim() !== "") {
            labelEmotion = String(ue.emotion).trim().toLowerCase();
            labelScores =
                ue.scores && typeof ue.scores === "object" && Object.keys(ue.scores).length > 0
                    ? ue.scores
                    : null;
            labelConf = conf(ue.confidence);
        }

        // Плавний перехід або ротація після відповіді
        if (avatarLayerA && avatarLayerB) {
            if (labelScores && Object.keys(labelScores).length > 0) {
                // Є повний розподіл від EmotionEngine — мікс-ротація з топ-2 емоцій
                startMultiEmotionRotation(labelScores, filename);
            } else if (displayEmotion === "neutral") {
                // LLM відповів нейтрально — легка ротація neutral-кліпів (muse, speak_blink, speak)
                startAvatarRotation("neutral", filename);
            } else {
                // Виразна емоція від LLM — ротація пулу цієї емоції
                startAvatarRotation(displayEmotion, filename);
            }
        }

        renderEmotionLabel(labelEmotion, labelScores, labelConf);
    }

    /**
     * Обробляє SSE-подію user_emotion (приходить одразу після status:thinking).
     * Показує «живу» реакцію аватара ще до відповіді LLM.
     */
    function handleUserEmotionEvent(data) {
        if (!data || !data.emotion) return;
        cancelThinkingAvatarDebounce();
        const c = Number(data.confidence) || 0;

        // Лейбл «Стан» оновлюємо завжди — щоб після аналізу одразу зникало «задумливо»
        renderEmotionLabel(data.emotion, data.scores || null, c);

        // Відео аватара і «липкий» стан — лише якщо емоція виразна
        if (data.emotion === "neutral" || c < USER_EMOTION_AVATAR_THRESHOLD) return;

        const filename =
            data.avatar_filename ||
            AVATAR_VIDEO_MAP[data.emotion] ||
            "muse.mp4";
        stickyUserAvatarThisTurn = {
            emotion: data.emotion,
            confidence: c,
            avatar_filename: filename,
            scores: data.scores || null,
        };
        // Якщо є повний розподіл — мікшуємо кліпи з топ-2 емоцій
        if (data.scores && Object.keys(data.scores).length > 0) {
            startMultiEmotionRotation(data.scores, filename);
        } else {
            startAvatarRotation(data.emotion, filename);
        }
    }

    function createThinkingBubble() {
        const wrap = document.createElement("div");
        wrap.className = "message bot assistant-thinking";
        wrap.innerHTML =
            '<div class="thinking-inner"><span class="thinking-label">Асистент думає</span><span class="thinking-dots"></span></div>';
        messagesContainer.appendChild(wrap);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
        return wrap;
    }

    function parseStoredUser() {
        const raw = localStorage.getItem("user");
        if (raw == null || raw === "") return null;
        try {
            let u = JSON.parse(raw);
            if (typeof u === "string") {
                u = JSON.parse(u);
            }
            return u && typeof u === "object" ? u : null;
        } catch {
            return null;
        }
    }

    const user = parseStoredUser();
    const userId = user?._id?.$oid || user?._id || user?.id || null;

    // --- 1. ТЕМА (світла / темна / як у системи) + меню в сайдбарі ---
    function normalizeThemePref(stored) {
        if (stored === "light" || stored === "dark" || stored === "system") return stored;
        return "dark";
    }

    function effectiveTheme(pref) {
        if (pref === "system") {
            return window.matchMedia("(prefers-color-scheme: dark)").matches
                ? "dark"
                : "light";
        }
        return pref === "light" ? "light" : "dark";
    }

    function applyThemePref(pref) {
        const p = normalizeThemePref(pref);
        localStorage.setItem("theme", p);
        document.body.setAttribute("data-theme", effectiveTheme(p));
        syncThemeSubmenuActive(p);
    }

    function syncThemeSubmenuActive(pref) {
        document.querySelectorAll(".sidebar-account-theme-btn[data-theme-pref]").forEach((btn) => {
            const v = btn.getAttribute("data-theme-pref");
            btn.classList.toggle("is-active", v === pref);
        });
    }

    function isHttpPhotoUrl(s) {
        const t = String(s || "").trim();
        if (!t) return false;
        try {
            const u = new URL(t);
            return u.protocol === "https:" || u.protocol === "http:";
        } catch {
            return false;
        }
    }

    function fillSidebarAccountBtn() {
        const wrap = document.getElementById("sidebar-account-wrap");
        const loginRow = document.getElementById("sidebar-login-row");
        const nameEl = document.getElementById("sidebar-user-name");
        const emailEl = document.getElementById("sidebar-user-email");
        const avEl = document.getElementById("sidebar-user-avatar");
        const photoEl = document.getElementById("sidebar-user-photo");
        if (!nameEl || !emailEl) return;

        const u = user;
        const emailStr = String(u?.email ?? "").trim();
        const nameStr = String(u?.username ?? "").trim();
        if (!u || (!emailStr && !nameStr)) {
            if (wrap) wrap.hidden = true;
            if (loginRow) loginRow.hidden = false;
            return;
        }
        if (wrap) wrap.hidden = false;
        if (loginRow) loginRow.hidden = true;

        const email = emailStr || "—";
        const rawName = (nameStr || email.split("@")[0] || "Користувач").trim();
        const displayName =
            rawName.length > 22 ? `${rawName.slice(0, 19)}…` : rawName;
        nameEl.textContent = displayName;
        emailEl.textContent = email;

        const pic = u.picture || u.avatarUrl || "";
        if (photoEl && avEl) {
            if (isHttpPhotoUrl(pic)) {
                photoEl.onerror = () => {
                    photoEl.onerror = null;
                    photoEl.removeAttribute("src");
                    photoEl.hidden = true;
                    avEl.removeAttribute("hidden");
                    const parts = rawName.split(/\s+/).filter(Boolean);
                    let ini = "?";
                    if (parts.length >= 2) {
                        ini = `${parts[0][0]}${parts[1][0]}`.toUpperCase();
                    } else if (rawName.length >= 2) {
                        ini = rawName.slice(0, 2).toUpperCase();
                    } else if (email.length >= 2 && email !== "—") {
                        ini = email.slice(0, 2).toUpperCase();
                    }
                    avEl.textContent = ini;
                };
                photoEl.src = pic;
                photoEl.hidden = false;
                avEl.setAttribute("hidden", "");
            } else {
                photoEl.removeAttribute("src");
                photoEl.hidden = true;
                avEl.removeAttribute("hidden");
                const parts = rawName.split(/\s+/).filter(Boolean);
                let initials = "?";
                if (parts.length >= 2) {
                    initials = `${parts[0][0]}${parts[1][0]}`.toUpperCase();
                } else if (rawName.length >= 2) {
                    initials = rawName.slice(0, 2).toUpperCase();
                } else if (email.length >= 2 && email !== "—") {
                    initials = email.slice(0, 2).toUpperCase();
                }
                avEl.textContent = initials;
            }
        }
    }

    function initSidebarAccountMenu() {
        const btn = document.getElementById("sidebar-account-btn");
        const wrap = document.getElementById("sidebar-account-wrap");
        const dropdown = document.getElementById("sidebar-account-dropdown");
        const themeSlot = document.getElementById("sidebar-theme-slot");
        const themeTrigger = document.getElementById("sidebar-theme-menu-trigger");
        const themeFlyout = document.getElementById("sidebar-theme-flyout");
        if (!btn || !wrap || !dropdown) return;

        function setThemeFlyout(open) {
            if (!themeSlot || !themeFlyout || !themeTrigger) return;
            themeSlot.classList.toggle("is-open", !!open);
            themeTrigger.setAttribute("aria-expanded", open ? "true" : "false");
            themeFlyout.hidden = !open;
        }

        function setOpen(open) {
            wrap.classList.toggle("is-open", open);
            btn.setAttribute("aria-expanded", open ? "true" : "false");
            dropdown.hidden = !open;
            if (!open) setThemeFlyout(false);
        }

        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            setOpen(dropdown.hidden);
        });

        themeTrigger?.addEventListener("click", (e) => {
            e.stopPropagation();
            if (!themeFlyout) return;
            setThemeFlyout(themeFlyout.hidden);
        });

        themeFlyout?.addEventListener("click", (e) => e.stopPropagation());

        themeFlyout?.querySelectorAll("[data-theme-pref]").forEach((el) => {
            el.addEventListener("click", (e) => {
                e.stopPropagation();
                const pref = el.getAttribute("data-theme-pref");
                if (pref) applyThemePref(pref);
                setThemeFlyout(false);
            });
        });

        dropdown.addEventListener("click", (e) => e.stopPropagation());

        const logoutOpen = document.getElementById("sidebar-dropdown-logout");
        logoutOpen?.addEventListener("click", (e) => {
            e.stopPropagation();
            setOpen(false);
            const modalEl = document.getElementById("logoutModal");
            if (modalEl && typeof bootstrap !== "undefined") {
                bootstrap.Modal.getOrCreateInstance(modalEl).show();
            }
        });

        document.addEventListener("click", () => setOpen(false));
        document.addEventListener("keydown", (e) => {
            if (e.key !== "Escape") return;
            if (themeFlyout && !themeFlyout.hidden) {
                setThemeFlyout(false);
                return;
            }
            setOpen(false);
        });

        window
            .matchMedia("(prefers-color-scheme: dark)")
            .addEventListener("change", () => {
                if (normalizeThemePref(localStorage.getItem("theme")) === "system") {
                    document.body.setAttribute(
                        "data-theme",
                        effectiveTheme("system")
                    );
                }
            });
    }

    applyThemePref(normalizeThemePref(localStorage.getItem("theme")));
    initSidebarAccountMenu();
    fillSidebarAccountBtn();

    // --- 2. ВИХІД ---
    confirmLogout?.addEventListener("click", () => { localStorage.clear(); window.location.href = "/"; });

    // --- 3. ЗГОРТАННЯ МЕНЮ ---
    toggleBtn?.addEventListener("click", () => {
        sidebar.classList.toggle("collapsed");
        mainContent.classList.toggle("expanded");
    });

    // Користувач — лише текст; бот — Markdown (історія / привітання / помилки).
    function appendMessage(role, text, emotionData) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `message ${role === "user" ? "user" : "bot"}`;
        if (role === "user") {
            const textEl = document.createElement("div");
            textEl.textContent = text;
            msgDiv.appendChild(textEl);
        } else {
            const inner = document.createElement("div");
            inner.className = "markdown-body bot-message-body";
            setMarkdownHtml(inner, text);
            msgDiv.appendChild(inner);
        }
        messagesContainer.appendChild(msgDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    /** Розбір URL для показу: хост + скорочений шлях (корінь сайту без зайвого «/»). */
    function splitLinkForDisplay(url) {
        try {
            const u = new URL(url);
            const host = u.hostname.replace(/^www\./i, "");
            let rest = `${u.pathname}${u.search}${u.hash}`;
            if (rest === "/" || rest === "") rest = "";
            else if (rest.length > 56) rest = `${rest.slice(0, 53)}…`;
            return { host, rest };
        } catch {
            return { host: url.slice(0, 40), rest: url.length > 40 ? "…" : "" };
        }
    }

    /** Людський заголовок для картки посилання з URL. */
    function linkTitleFromUrl(url) {
        try {
            const u = new URL(url);
            const h = u.hostname.replace(/^www\./i, "");
            const mGurt =
                u.pathname.match(/\/gurt(\d+)\/?/i) ||
                u.pathname.match(/gurt(\d+)/i);
            if (mGurt && /studmisto\.kpi\.ua$/i.test(h)) {
                return `Гуртожиток №${mGurt[1]}`;
            }
            if (/rozklad\.kpi\.ua$/i.test(h)) return "Розклад занять (КПІ)";
            if (/studmisto\.kpi\.ua$/i.test(h)) return "Студмістечко КПІ";
            if (/kpi\.ua$/i.test(h)) {
                return h.replace(".kpi.ua", "").replace(/^./, (c) => c.toUpperCase()) + " — КПІ";
            }
            return h;
        } catch {
            return "";
        }
    }

    function pickLinkCardTitle(url, chatTitle) {
        const fromUrl = linkTitleFromUrl(url);
        if (fromUrl && /гуртожиток|студмістечко|розклад/i.test(fromUrl)) {
            return fromUrl;
        }
        const t = (chatTitle || "").trim();
        if (t && t !== "Без назви" && t.length < 72) return t;
        return fromUrl || "Відкрити джерело";
    }

    function linkCardIconClass(url) {
        const u = (url || "").toLowerCase();
        if (u.includes("gurt")) return "bi bi-house-door-fill";
        if (u.includes("rozklad")) return "bi bi-calendar3";
        if (u.includes("studmisto")) return "bi bi-building";
        return "bi bi-link-45deg";
    }

    /** Повідомлення асистента: текст, опційно посилання (окремо від тексту). */
    function appendAssistantMessage(msg) {
        const wrap = document.createElement("div");
        wrap.className = "message bot";

        const body = document.createElement("div");
        body.className = "bot-message-body markdown-body";
        setMarkdownHtml(body, msg.text || "");
        wrap.appendChild(body);

        const linkVal = (msg.link || "").trim();
        if (isHttpUrl(linkVal)) {
            const { host, rest } = splitLinkForDisplay(linkVal);
            const card = document.createElement("a");
            card.href = linkVal;
            card.target = "_blank";
            card.rel = "noopener noreferrer";
            card.className = "bot-message-link-card";
            card.setAttribute("title", `Відкрити: ${linkVal}`);

            const iconCell = document.createElement("span");
            iconCell.className = "bot-message-link-icon-cell";
            iconCell.setAttribute("aria-hidden", "true");
            const linkIcon = document.createElement("i");
            linkIcon.className = linkCardIconClass(linkVal);
            iconCell.appendChild(linkIcon);

            const mid = document.createElement("span");
            mid.className = "bot-message-link-mid";

            const headline = document.createElement("span");
            headline.className = "bot-message-link-headline";
            headline.textContent = pickLinkCardTitle(linkVal, msg.title);

            const kicker = document.createElement("span");
            kicker.className = "bot-message-link-kicker";
            kicker.textContent = "Офіційне джерело";

            const urlLine = document.createElement("span");
            urlLine.className = "bot-message-link-url-line";
            urlLine.textContent = rest ? `${host}${rest}` : host;

            mid.appendChild(headline);
            mid.appendChild(kicker);
            mid.appendChild(urlLine);

            const ext = document.createElement("span");
            ext.className = "bot-message-link-external";
            ext.setAttribute("aria-hidden", "true");
            const extIcon = document.createElement("i");
            extIcon.className = "bi bi-box-arrow-up-right";
            ext.appendChild(extIcon);

            card.appendChild(iconCell);
            card.appendChild(mid);
            card.appendChild(ext);
            wrap.appendChild(card);
        }

        messagesContainer.appendChild(wrap);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function renderHistoryMessage(msg) {
        if (msg.role === "user") {
            appendMessage("user", msg.text);
            return;
        }
        if (msg.emotion != null || (msg.link != null && String(msg.link).trim())) {
            appendAssistantMessage({
                text: msg.text,
                link: msg.link || "",
                title: msg.title || "",
            });
            return;
        }
        appendMessage("bot", msg.text);
    }

    // ІНІЦІАЛІЗАЦІЯ СЕСІЇ (Завантаження історії)
    async function initSession() {
        let sessionId = localStorage.getItem("sessionId");

        if (!sessionId || sessionId === "null" || sessionId === "undefined") {
            try {
                const res = await fetch(`${API_BASE_URL}/sessions`, {
                    method: "POST",
                    headers: authHeaders({ "Content-Type": "application/json" }),
                    body: JSON.stringify({
                        userId: userId,
                        name: "Новий чат",
                        messages: [],
                        createdAt: new Date().toISOString(),
                        updatedAt: new Date().toISOString()
                    }),
                });
                if (res.status === 401) {
                    window.location.href = "/auth";
                    return;
                }
                if (res.ok) {
                    const rawId = await res.text();
                    sessionId = rawId.replace(/^"|"$/g, '');
                    localStorage.setItem("sessionId", sessionId);
                }
            } catch (e) { console.error("Помилка створення початкової сесії", e); }
        }

        if (!sessionId || sessionId === "null" || sessionId === "undefined") {
            messagesContainer.innerHTML = "";
            appendMessage(
                "bot",
                "Не вдалося відкрити чат (немає сесії). Увійдіть знову або натисніть «Новий чат»."
            );
            return;
        }

        // Завантажуємо повідомлення
        try {
            const msgRes = await fetch(`${API_BASE_URL}/sessions/${sessionId}`, {
                headers: authHeaders(),
            });
            if (msgRes.status === 401) {
                window.location.href = "/auth";
                return;
            }
            if (msgRes.status === 404) {
                localStorage.removeItem("sessionId");
                await initSession();
                return;
            }
            if (msgRes.ok) {
                const sessionData = await msgRes.json();
                messagesContainer.innerHTML = "";

                if (sessionData.messages && sessionData.messages.length > 0) {
                    sessionData.messages.forEach(renderHistoryMessage);
                } else {
                    appendMessage(
                        "bot",
                        `Привіт, ${user.username || "студенте"}! Чим можу допомогти?`
                    );
                }
            } else {
                messagesContainer.innerHTML = "";
                appendMessage(
                    "bot",
                    "Не вдалося завантажити історію чату. Спробуйте вийти й увійти знову."
                );
            }
        } catch (e) { 
            console.error("Помилка завантаження повідомлень", e);
            appendMessage("bot", "Помилка зв'язку з сервером."); 
        }
        requestAnimationFrame(() => syncAvatarDefaultBottom());
    }

    function syncAttachButton() {
        if (attachBtn) {
            attachBtn.classList.toggle("has-file", !!pendingFile);
            attachBtn.title = pendingFile
                ? `Вкладено: ${pendingFile.name} (клацніть скрепку, щоб змінити)`
                : "Додати PDF або зображення";
        }
        if (pendingFileBar && pendingFileNameEl) {
            if (pendingFile) {
                pendingFileBar.hidden = false;
                pendingFileNameEl.textContent = pendingFile.name;
            } else {
                pendingFileBar.hidden = true;
                pendingFileNameEl.textContent = "";
            }
        }
        if (userInput) {
            const normal =
                userInput.dataset.placeholderNormal || "Напишіть повідомлення…";
            const withFile =
                userInput.dataset.placeholderWithFile ||
                "Промпт до файлу (необов’язково)…";
            userInput.placeholder = pendingFile ? withFile : normal;
        }
        requestAnimationFrame(() => syncAvatarDefaultBottom());
    }

    attachBtn?.addEventListener("click", () => {
        fileInput?.click();
    });
    fileInput?.addEventListener("change", () => {
        const f = fileInput.files && fileInput.files[0];
        fileInput.value = "";
        if (!f) return;
        pendingFile = f;
        syncAttachButton();
        userInput?.focus();
    });
    pendingFileRemoveBtn?.addEventListener("click", (e) => {
        e.preventDefault();
        pendingFile = null;
        syncAttachButton();
    });

    /** Голос: Web Speech API у браузері (Chrome / Edge / Safari). Без OpenAI і без відправки аудіо на ваш сервер. */
    function getSpeechRecognitionCtor() {
        return window.SpeechRecognition || window.webkitSpeechRecognition || null;
    }

    let speechRec = null;
    let voiceListening = false;
    let voiceExplicitStop = false;

    function stopVoiceInput(options = {}) {
        const sendAfter = options.sendAfter === true;
        if (!speechRec && !voiceListening) {
            if (sendAfter) {
                const t = userInput.value.trim();
                if (t) sendMessage(t);
            }
            return;
        }
        voiceExplicitStop = true;
        voiceListening = false;
        voiceBtn?.classList.remove("listening");
        voiceBtn?.setAttribute("aria-pressed", "false");
        const rec = speechRec;
        speechRec = null;
        if (rec) {
            try {
                rec.stop();
            } catch (e) {
                /* ignore */
            }
        }
        if (sendAfter) {
            const t = userInput.value.trim();
            if (t) sendMessage(t);
        }
    }

    function startVoiceInput() {
        const Ctor = getSpeechRecognitionCtor();
        if (!Ctor) {
            appendMessage(
                "bot",
                "Голос у цьому браузері недоступний. Спробуйте **Google Chrome**, **Microsoft Edge** або **Safari** — розпізнавання йде в браузері, **OpenAI не використовується**."
            );
            return;
        }
        if (chatInFlight) return;

        if (speechRec) {
            try {
                speechRec.stop();
            } catch (e) {
                /* ignore */
            }
            speechRec = null;
        }

        voiceExplicitStop = false;
        voiceListening = true;
        voiceBtn?.classList.add("listening");
        voiceBtn?.setAttribute("aria-pressed", "true");

        const rec = new Ctor();
        speechRec = rec;
        rec.lang = "uk-UA";
        rec.continuous = true;
        rec.interimResults = true;

        let finalTranscript = "";
        rec.onresult = (event) => {
            let interim = "";
            for (let i = event.resultIndex; i < event.results.length; i += 1) {
                const piece = event.results[i][0].transcript;
                if (event.results[i].isFinal) finalTranscript += piece;
                else interim += piece;
            }
            userInput.value = (finalTranscript + interim).trimStart();
        };

        rec.onerror = (event) => {
            if (event.error === "aborted" || event.error === "no-speech") return;
            voiceListening = false;
            voiceBtn?.classList.remove("listening");
            voiceBtn?.setAttribute("aria-pressed", "false");
            speechRec = null;
            const hint =
                event.error === "not-allowed"
                    ? "Дозвольте доступ до мікрофона для цього сайту в налаштуваннях браузера."
                    : `Помилка розпізнавання (${event.error}).`;
            appendMessage("bot", hint);
        };

        rec.onend = () => {
            voiceBtn?.classList.remove("listening");
            voiceBtn?.setAttribute("aria-pressed", "false");
            speechRec = null;
            const skipAuto = voiceExplicitStop;
            voiceExplicitStop = false;
            voiceListening = false;
            if (skipAuto) return;
            const t = userInput.value.trim();
            if (t && !chatInFlight) sendMessage(t);
        };

        try {
            rec.start();
        } catch (err) {
            voiceListening = false;
            voiceBtn?.classList.remove("listening");
            voiceBtn?.setAttribute("aria-pressed", "false");
            speechRec = null;
            console.warn("SpeechRecognition.start", err);
            const extra =
                typeof location !== "undefined" &&
                location.protocol !== "https:" &&
                location.hostname !== "localhost" &&
                location.hostname !== "127.0.0.1"
                    ? " Для голосу потрібен **HTTPS** (або localhost)."
                    : "";
            appendMessage(
                "bot",
                `Не вдалося увімкнути мікрофон (${String(err)}). Перевірте дозвіл на мікрофон у браузері.${extra}`
            );
        }
    }

    voiceBtn?.addEventListener("click", () => {
        if (getSpeechRecognitionCtor() == null) {
            appendMessage(
                "bot",
                "У **Firefox** немає Web Speech API для розпізнавання. Відкрийте чат у Chrome / Edge / Safari — голос обробляє **лише браузер**, без ключів на сервері."
            );
            return;
        }
        if (voiceListening) {
            stopVoiceInput({ sendAfter: true });
            return;
        }
        startVoiceInput();
    });

    // ВІДПРАВКА ПОВІДОМЛЕННЯ (SSE-потік)
    async function sendMessage(overrideText) {
        stopVoiceInput();
        const textRaw =
            typeof overrideText === "string"
                ? overrideText.trim()
                : userInput.value.trim();
        const sessionId = localStorage.getItem("sessionId");
        if ((!textRaw && !pendingFile) || !sessionId || chatInFlight) return;

        const textForApi = textRaw || "Проаналізуй вкладений файл.";
        const fileToSend = pendingFile;
        const userBubble = fileToSend
            ? `${textForApi}\n\n📎 ${fileToSend.name}`
            : textRaw;

        // emotionData буде додано після отримання user_emotion event
        let _pendingUserEmotionData = null;
        flushStreamingMathTimer();
        // Тимчасово додаємо повідомлення без бейджу (оновимо після user_emotion)
        appendMessage("user", userBubble, null);
        userInput.value = "";
        pendingFile = null;
        syncAttachButton();

        chatInFlight = true;
        stickyUserAvatarThisTurn = null;
        clearAvatarRotation();
        streamAbortController = new AbortController();
        setSendButtonMode("stop");
        if (attachBtn) attachBtn.disabled = true;
        if (voiceBtn) voiceBtn.disabled = true;
        if (userInput) userInput.disabled = true;

        const thinkingEl = createThinkingBubble();
        let streamBubble = null;
        let streamBody = null;
        let accumulated = "";

        const cleanupThinking = () => {
            thinkingEl?.remove();
        };

        const ensureStreamBubble = () => {
            cleanupThinking();
            if (streamBubble) return;
            streamBubble = document.createElement("div");
            streamBubble.className = "message bot assistant-streaming";
            streamBody = document.createElement("div");
            streamBody.className = "streaming-body markdown-body";
            streamBody.innerHTML = "";
            streamBubble.appendChild(streamBody);
            messagesContainer.appendChild(streamBubble);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        };

        try {
            let res;
            if (fileToSend) {
                const fd = new FormData();
                fd.append("message", textForApi);
                fd.append("sessionId", sessionId);
                fd.append("userId", userId);
                fd.append("file", fileToSend, fileToSend.name);
                res = await fetch(`${API_BASE_URL}/faq/chat/stream/upload`, {
                    method: "POST",
                    headers: { Accept: "text/event-stream" },
                    body: fd,
                    signal: streamAbortController.signal,
                });
            } else {
                res = await fetch(`${API_BASE_URL}/faq/chat/stream`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "text/event-stream",
                    },
                    body: JSON.stringify({
                        message: textForApi,
                        sessionId,
                        userId,
                    }),
                    signal: streamAbortController.signal,
                });
            }

            if (!res.ok) {
                cleanupThinking();
                clearAvatarRotation();
                let errMsg = `Помилка сервера (${res.status}). Спробуйте ще раз.`;
                try {
                    const j = await res.json();
                    if (j.detail) {
                        errMsg =
                            typeof j.detail === "string"
                                ? j.detail
                                : Array.isArray(j.detail)
                                  ? j.detail
                                        .map((x) => x.msg || JSON.stringify(x))
                                        .join("; ")
                                  : JSON.stringify(j.detail);
                    }
                } catch (_) {
                    /* не JSON */
                }
                appendMessage("bot", errMsg);
                return;
            }

            await consumeSseStream(res, (ev) => {
                if (ev.type === "status" && ev.phase === "thinking") {
                    if (emotionLabel) {
                        const tm = EMOTION_META.thinking;
                        emotionLabel.dataset.state = "thinking";
                        emotionLabel.innerHTML = tm
                            ? `<span style="color:${tm.color};font-weight:600">${tm.emoji}&nbsp;${tm.label}…</span>`
                            : "думає…";
                    }
                    scheduleThinkingAvatarRotation();
                    return;
                }
                // user_emotion — реакція аватара ще до відповіді LLM (від EmotionEngine)
                if (ev.type === "user_emotion") {
                    _pendingUserEmotionData = ev;
                    handleUserEmotionEvent(ev);
                    return;
                }
                if (ev.type === "delta" && ev.content) {
                    accumulated += ev.content;
                    ensureStreamBubble();
                    const preview = extractMainStreamPreview(accumulated);
                    if (streamBody) {
                        if (preview == null) {
                            flushStreamingMathTimer();
                            streamBody.textContent = "…";
                        } else {
                            setStreamingMarkdownHtml(streamBody, preview);
                        }
                    }
                    messagesContainer.scrollTop =
                        messagesContainer.scrollHeight;
                }
                if (ev.type === "done") {
                    cleanupThinking();
                    flushStreamingMathTimer();
                    streamBubble?.remove();
                    streamBubble = null;
                    streamBody = null;
                    appendAssistantMessage({
                        text: ev.response || "",
                        link: ev.link || "",
                        title: ev.title || "",
                    });
                    clearAvatarRotation();
                    applyAvatarEmotion(ev);
                }
                if (ev.type === "error") {
                    clearAvatarRotation();
                    streamBubble?.remove();
                    cleanupThinking();
                    appendMessage(
                        "bot",
                        ev.detail || "Помилка під час відповіді асистента."
                    );
                }
            });
        } catch (e) {
            streamBubble?.remove();
            cleanupThinking();
            clearAvatarRotation();
            const aborted =
                e &&
                (e.name === "AbortError" ||
                    (typeof DOMException !== "undefined" &&
                        e instanceof DOMException &&
                        e.name === "AbortError"));
            if (aborted) {
                appendMessage(
                    "bot",
                    "_Генерацію зупинено._ Можете надіслати наступне повідомлення."
                );
            } else {
                console.error("Помилка відправки", e);
                appendMessage("bot", "Помилка відправки.");
            }
        } finally {
            chatInFlight = false;
            clearAvatarRotation();
            streamAbortController = null;
            setSendButtonMode("send");
            if (attachBtn) attachBtn.disabled = false;
            if (voiceBtn) voiceBtn.disabled = false;
            if (userInput) userInput.disabled = false;
            if (emotionLabel && emotionLabel.dataset.state === "thinking") {
                emotionLabel.dataset.state = "idle";
                emotionLabel.innerHTML = "Очікування";
            }
            requestAnimationFrame(() => syncAvatarDefaultBottom());
        }
    }

    // Події кнопок
    sendBtn?.addEventListener("click", () => {
        if (chatInFlight && streamAbortController) {
            streamAbortController.abort();
            return;
        }
        sendMessage();
    });
    userInput?.addEventListener("keypress", (e) => { 
        if (e.key === "Enter") {
            e.preventDefault();
            sendMessage();
        }
    });

    // --- 5. DRAG & RESIZE АВАТАРА ---
    if (avatarBox) {
        let isDragging = false, isResizing = false, startX, startY, initialL, initialT, initialW;
        resizeHandle?.addEventListener('mousedown', (e) => { e.stopPropagation(); isResizing = true; startX = e.clientX; initialW = avatarBox.offsetWidth; avatarBox.style.transition = 'none'; });
        avatarBox.addEventListener('mousedown', (e) => { if (isResizing) return; isDragging = true; avatarBox.style.transition = 'none'; const r = avatarBox.getBoundingClientRect(); initialL = r.left; initialT = r.top; startX = e.clientX; startY = e.clientY; avatarBox.style.top = initialT+'px'; avatarBox.style.left = initialL+'px'; avatarBox.style.bottom = 'auto'; avatarBox.style.right = 'auto'; });
        document.addEventListener('mousemove', (e) => {
            if (isResizing) { const w = initialW + (e.clientX - startX); if (w >= 150 && w <= 450) avatarBox.style.width = w + 'px'; }
            else if (isDragging) { avatarBox.style.left = (initialL + (e.clientX - startX)) + 'px'; avatarBox.style.top = (initialT + (e.clientY - startY)) + 'px'; }
        });
        document.addEventListener('mouseup', () => { isDragging = false; isResizing = false; avatarBox.style.transition = 'box-shadow 0.4s ease, border-color 0.4s ease, transform 0.3s'; });
    }

    newChatBtn?.addEventListener("click", async (e) => {
        if (!userId) { window.location.href = "/auth"; return; }
        e.preventDefault();
        try {
            const res = await fetch(`${API_BASE_URL}/sessions/newSession`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({
                    userId,
                    name: "Новий чат",
                    messages: [],
                    createdAt: new Date().toISOString(),
                    updatedAt: new Date().toISOString(),
                }),
            });
            if (res.ok) { const id = await res.text(); localStorage.setItem("sessionId", id.replace(/^"|"$/g, '')); window.location.href = "/chat"; }
        } catch (e) { console.error(e); }
    });

    if (window.location.pathname.includes('chat')) initSession();
});
/* ════════════════════════════════════════════════════════════════════════════
 * frontend/js/emotion-demo.js
 * ────────────────────────────────────────────────────────────────────────────
 * Логіка інтерактивної демо-сторінки модуля аналізу емоцій.
 *
 * Що робить:
 *   1. Перевіряє статус ML-моделі через GET /api/emotion/health
 *   2. Підвантажує опис алгоритму через GET /api/emotion/info
 *   3. Виконує POST /api/emotion/analyze при кліку «Аналізувати»
 *   4. Візуалізує:
 *        – вибрану емоцію + впевненість
 *        – розподіл по 7 класах (стовпці softmax)
 *        – внески компонентів (lexicon / pattern / ml)
 *        – токени, що вплинули на рішення
 *        – зміну анімації 3D-аватара (з плавним фейдом)
 *
 * Залежить від CSS-класів у frontend/css/emotion-demo.css.
 * ══════════════════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    const API = {
        analyze: "/api/emotion/analyze",
        info: "/api/emotion/info",
        health: "/api/emotion/health",
    };

    // Метадані емоцій (синхронізовано з emotion_engine.py / chat.js)
    const EMOTION_META = {
        happy:    { emoji: "😊", label: "радість",     color: "#f5a623" },
        sad:      { emoji: "😔", label: "смуток",      color: "#7b9dc8" },
        surprise: { emoji: "😲", label: "здивування",  color: "#a855f7" },
        thinking: { emoji: "🤔", label: "роздуми",     color: "#10b981" },
        neutral:  { emoji: "😐", label: "нейтрально",  color: "#6b7280" },
        angry:    { emoji: "😠", label: "злість",      color: "#ef4444" },
        disgust:  { emoji: "🤢", label: "огида",       color: "#84cc16" },
    };

    const AVATAR_VIDEO = {
        neutral: "muse.mp4",
        happy: "excited.mp4",
        sad: "sad.mp4",
        surprise: "surprize1.mp4",
        thinking: "squinted1.mp4",
        angry: "angry.mp4",
        disgust: "disgust.mp4",
    };

    const ORDER = [
        "happy",
        "sad",
        "surprise",
        "thinking",
        "angry",
        "disgust",
        "neutral",
    ];

    // ── DOM ──────────────────────────────────────────────────────────────────
    const $text         = document.getElementById("ed-text");
    const $analyzeBtn   = document.getElementById("ed-analyze-btn");
    const $resetCtx     = document.getElementById("ed-reset-context");
    const $statusDot    = document.querySelector("#ed-status .ed-status-dot");
    const $statusText   = document.getElementById("ed-status-text");
    const $infoBody     = document.getElementById("ed-info-body");

    const $avatarVideo  = document.getElementById("ed-avatar-video");
    const $avatarEmoji  = document.getElementById("ed-avatar-emoji");
    const $avatarLabel  = document.getElementById("ed-avatar-label");
    const $avatarConf   = document.getElementById("ed-avatar-conf");
    const $avatarFile   = document.getElementById("ed-avatar-file");
    const $avatarPriority = document.getElementById("ed-avatar-priority");

    /** Поточний .mp4: порівнюємо файл, не клас (у одного класу до 3 рівнів кліпа). */
    function initialAvatarFilename() {
        const sourceEl = $avatarVideo && $avatarVideo.querySelector("source");
        if (!sourceEl || !sourceEl.src) return "";
        try {
            const path = decodeURIComponent(
                new URL(sourceEl.src, window.location.href).pathname
            );
            const seg = path.split("/").pop() || "";
            return seg;
        } catch (_) {
            return "";
        }
    }

    let currentAvatarFilename = initialAvatarFilename();

    const $distCard     = document.getElementById("ed-distribution-card");
    const $bars         = document.getElementById("ed-bars");
    const $methodBadge  = document.getElementById("ed-method-badge");

    const $compLex      = document.getElementById("ed-comp-lex");
    const $compPat      = document.getElementById("ed-comp-pat");
    const $compMl       = document.getElementById("ed-comp-ml");

    const $detCard      = document.getElementById("ed-details-card");
    const $detEmotion   = document.getElementById("ed-detail-emotion");
    const $detConf      = document.getElementById("ed-detail-conf");
    const $detMethod    = document.getElementById("ed-detail-method");
    const $detSmoothed  = document.getElementById("ed-detail-smoothed");
    const $detMl        = document.getElementById("ed-detail-ml");
    const $detTime      = document.getElementById("ed-detail-time");
    const $rawJson      = document.getElementById("ed-raw-json");

    // ── 1. Health check (стан ML-моделі) ──────────────────────────────────────
    async function pingHealth() {
        try {
            const res = await fetch(API.health);
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            if (data.ml_loaded) {
                setStatus("ok", `ML-модель активна · ${data.emotions.length} класів`);
            } else if (data.ml_available) {
                setStatus("warn", "ML-модель не натренована (rule-based фолбек)");
            } else {
                setStatus("warn", "scikit-learn не встановлено (rule-based)");
            }
        } catch (err) {
            setStatus("error", "Сервіс недоступний");
            console.warn("health", err);
        }
    }

    function setStatus(state, text) {
        if ($statusDot) $statusDot.dataset.state = state;
        if ($statusText) $statusText.textContent = text;
    }

    // ── 2. Загрузка інформації про алгоритм ──────────────────────────────────
    async function loadAlgorithmInfo() {
        try {
            const res = await fetch(API.info);
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            renderInfo(data);
        } catch (err) {
            console.warn("info", err);
            $infoBody.innerHTML = "<p>Не вдалося завантажити опис алгоритму.</p>";
        }
    }

    function renderInfo(data) {
        const components = (data.components || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("");
        const pipeline = (data.pipeline || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("");
        $infoBody.innerHTML = `
            <p>${escapeHtml(data.description || "")}</p>
            <h3><i class="bi bi-puzzle"></i> Алгоритм</h3>
            <p><code>${escapeHtml(data.algorithm)}</code></p>
            <h3><i class="bi bi-list-stars"></i> Компоненти</h3>
            <ul>${components}</ul>
            <h3><i class="bi bi-diagram-2"></i> Пайплайн обробки</h3>
            <ol>${pipeline}</ol>
        `;
    }

    // ── 3. Виконання аналізу ──────────────────────────────────────────────────
    async function runAnalyze() {
        const text = ($text.value || "").trim();
        if (!text) {
            $text.focus();
            return;
        }
        $analyzeBtn.disabled = true;
        const originalLabel = $analyzeBtn.innerHTML;
        $analyzeBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Аналіз…';

        const t0 = performance.now();
        try {
            const res = await fetch(API.analyze, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text,
                    reset_context: $resetCtx.checked,
                    demo_responsive_avatar: true,
                }),
            });
            if (!res.ok) {
                const errTxt = await res.text();
                throw new Error(`HTTP ${res.status}: ${errTxt}`);
            }
            const data = await res.json();
            const t1 = performance.now();
            renderResult(data, Math.round(t1 - t0));
        } catch (err) {
            console.error("analyze", err);
            setStatus("error", "Помилка аналізу — " + err.message);
        } finally {
            $analyzeBtn.disabled = false;
            $analyzeBtn.innerHTML = originalLabel;
        }
    }

    // ── 4. Рендер результату ──────────────────────────────────────────────────
    function renderResult(data, elapsedMs) {
        // 4.1 Аватар
        applyAvatar(data);

        // 4.2 Розподіл по класах
        $distCard.hidden = false;
        $bars.innerHTML = "";
        ORDER.forEach((emotion) => {
            const score = data.scores[emotion] ?? 0;
            const isWinner = emotion === data.emotion;
            $bars.appendChild(makeBar(emotion, score, { isWinner, mini: false }));
        });
        $methodBadge.textContent = data.method;

        // 4.3 Внески компонентів
        renderComponent($compLex, data.component_scores?.lexicon, data.tokens_matched);
        renderComponent($compPat, data.component_scores?.pattern, null);
        renderComponent($compMl,  data.component_scores?.ml,      null);

        // 4.4 Деталі
        $detCard.hidden = false;
        const meta = EMOTION_META[data.emotion] || EMOTION_META.neutral;
        $detEmotion.innerHTML = `${meta.emoji} ${meta.label}`;
        $detEmotion.style.color = meta.color;
        $detConf.textContent = `${(data.confidence * 100).toFixed(1)} %`;
        $detMethod.textContent = data.method;
        $detSmoothed.textContent = data.context_smoothed ? "так" : "ні";
        $detMl.textContent = data.ml_used ? "так" : "ні";
        $detTime.textContent = `${elapsedMs} мс`;
        $rawJson.textContent = JSON.stringify(data, null, 2);

        // Прокрутити до результату
        $distCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function renderComponent($card, scores, tokens) {
        if (!$card) return;
        const $bars = $card.querySelector(".ed-comp-bars");
        const $tokensEl = $card.querySelector(".ed-comp-tokens");

        if (!scores || allZero(scores)) {
            $card.hidden = !tokens || tokens.length === 0;
            if (!$card.hidden) {
                $bars.innerHTML = '<div class="ed-text-muted" style="font-size:12px;">— нульовий внесок</div>';
            }
            if ($tokensEl) $tokensEl.innerHTML = "";
            if (!tokens || tokens.length === 0) return;
        } else {
            $card.hidden = false;
            $bars.innerHTML = "";
            const max = Math.max(0.01, ...Object.values(scores));
            ORDER.forEach((emotion) => {
                const v = scores[emotion] ?? 0;
                const ratio = v / max;
                $bars.appendChild(makeBar(emotion, ratio, { mini: true, raw: v }));
            });
        }

        // Токени (для лексикона)
        if ($tokensEl && Array.isArray(tokens) && tokens.length) {
            $tokensEl.innerHTML = tokens
                .slice(0, 24)
                .map((t) => `<span class="ed-token-chip">${escapeHtml(String(t))}</span>`)
                .join("");
        } else if ($tokensEl) {
            $tokensEl.innerHTML = "";
        }
    }

    function makeBar(emotion, value, { isWinner = false, mini = false, raw = null } = {}) {
        const meta = EMOTION_META[emotion] || EMOTION_META.neutral;
        const pct = Math.max(0, Math.min(100, value * 100));
        const root = document.createElement("div");
        root.className = mini ? "ed-mini-bar" : "ed-bar";
        if (isWinner) root.classList.add("is-winner");

        const label = document.createElement("div");
        label.className = mini ? "" : "ed-bar-label";
        label.innerHTML = mini
            ? `<span style="color:${meta.color}; font-weight:600;">${meta.emoji} ${meta.label}</span>`
            : `<span class="emoji">${meta.emoji}</span><span>${meta.label}</span>`;
        root.appendChild(label);

        const track = document.createElement("div");
        track.className = mini ? "ed-mini-track" : "ed-bar-track";
        const fill = document.createElement("div");
        fill.className = mini ? "ed-mini-fill" : "ed-bar-fill";
        fill.style.width = `${pct}%`;
        fill.style.background = meta.color;
        fill.style.color = meta.color;
        track.appendChild(fill);
        root.appendChild(track);

        const valEl = document.createElement("div");
        valEl.className = mini ? "" : "ed-bar-value";
        if (mini) {
            valEl.style.fontVariantNumeric = "tabular-nums";
            valEl.style.color = "var(--text-secondary)";
            valEl.style.fontSize = "11px";
            valEl.style.textAlign = "right";
            valEl.textContent = raw != null ? raw.toFixed(2) : (value).toFixed(2);
        } else {
            valEl.textContent = `${pct.toFixed(1)}%`;
        }
        root.appendChild(valEl);
        return root;
    }

    const AVATAR_TIER_LABEL = {
        1: "делікатно",
        2: "помірно",
        3: "найвиразніше",
    };

    function applyAvatar(data) {
        const meta = EMOTION_META[data.emotion] || EMOTION_META.neutral;
        const filename =
            (data.avatar && data.avatar.filename) ||
            AVATAR_VIDEO[data.emotion] ||
            AVATAR_VIDEO.neutral;
        const priority = (data.avatar && data.avatar.priority) ?? "—";
        const priorityText =
            typeof priority === "number" && AVATAR_TIER_LABEL[priority]
                ? `${priority} (${AVATAR_TIER_LABEL[priority]})`
                : String(priority);

        $avatarEmoji.textContent = meta.emoji;
        $avatarLabel.textContent = meta.label;
        $avatarConf.textContent = `впевненість: ${(data.confidence * 100).toFixed(1)} %`;
        $avatarLabel.style.color = meta.color;
        $avatarFile.textContent = filename;
        $avatarPriority.textContent = priorityText;

        if (!$avatarVideo) return;
        const sourceEl = $avatarVideo.querySelector("source");
        if (!sourceEl) return;

        if (filename === currentAvatarFilename) {
            $avatarVideo.currentTime = 0;
            $avatarVideo.play().catch(() => {});
            return;
        }

        currentAvatarFilename = filename;
        $avatarVideo.style.opacity = "0";
        setTimeout(() => {
            sourceEl.src = `/avatar/animations/${filename}`;
            $avatarVideo.load();
            $avatarVideo.play().catch(() => {});
            $avatarVideo.style.opacity = "1";
        }, 300);
    }

    function allZero(obj) {
        if (!obj) return true;
        return Object.values(obj).every((v) => !v || v === 0);
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    // ── 5. Презети ───────────────────────────────────────────────────────────
    document.querySelectorAll(".ed-preset").forEach((btn) => {
        btn.addEventListener("click", () => {
            $text.value = btn.dataset.text || "";
            $text.focus();
            runAnalyze();
        });
    });

    // ── 6. Кнопка / Enter ────────────────────────────────────────────────────
    $analyzeBtn.addEventListener("click", runAnalyze);
    $text.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            runAnalyze();
        }
    });

    // ── 7. Тема (sync з рештою додатку) ──────────────────────────────────────
    const storedTheme = localStorage.getItem("theme") || "dark";
    if (storedTheme === "light") document.body.setAttribute("data-theme", "light");

    // ── Запуск ──────────────────────────────────────────────────────────────
    pingHealth();
    loadAlgorithmInfo();
})();

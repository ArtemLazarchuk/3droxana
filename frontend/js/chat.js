window.addEventListener("DOMContentLoaded", async () => {
    const API_BASE_URL = "/api";

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
    const avatarVideo = document.getElementById("avatar-video");
    const emotionLabel = document.getElementById("emotion-status");
    const newChatBtn = document.getElementById("new-chat-btn");
    const avatarBox = document.querySelector('.avatar-fixed'); // Блок для підсвітки

    function applyAvatarEmotion(data) {
        if (avatarBox) {
            avatarBox.classList.add("active-glow");
            setTimeout(() => avatarBox.classList.remove("active-glow"), 3000);
        }
        if (data.emotion && avatarVideo) {
            const videoMap = {
                neutral: "speak_blink.mp4",
                happy: "happy.mp4",
                sad: "sad.mp4",
                surprise: "surprize1.mp4",
                thinking: "squinted1.mp4",
                "😊": "happy.mp4",
                "😄": "speak_blink.mp4",
                "😲": "surprize1.mp4",
                "🤔": "squinted1.mp4",
                "😍": "happy.mp4",
            };
            const key = String(data.emotion).trim();
            const filename =
                videoMap[key] || videoMap[key.toLowerCase()] || "speak_blink.mp4";
            const sourceElement = avatarVideo.querySelector("source");
            if (sourceElement && !sourceElement.src.includes(filename)) {
                sourceElement.src = `/avatar/animations/${filename}`;
                avatarVideo.load();
                avatarVideo.play().catch((e) => console.log("Помилка відео:", e));
                if (emotionLabel) emotionLabel.textContent = data.emotion;
            }
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

    const user = JSON.parse(localStorage.getItem("user"));
    const userId = user?._id?.$oid || user?._id || null;

    if (!user) {
        window.location.href = "/auth";
        return;
    }

    // Тогл меню (Sidebar)
    toggleBtn?.addEventListener("click", () => {
        sidebar.classList.toggle("collapsed");
        mainContent.classList.toggle("expanded");
    });

    // Користувач — лише текст; бот — Markdown (історія / привітання / помилки).
    function appendMessage(role, text) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `message ${role === "user" ? "user" : "bot"}`;
        if (role === "user") {
            msgDiv.textContent = text;
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
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        userId: userId,
                        name: "Новий чат",
                        messages: [],
                        createdAt: new Date().toISOString(),
                        updatedAt: new Date().toISOString()
                    }),
                });
                if (res.ok) {
                    const rawId = await res.text();
                    sessionId = rawId.replace(/^"|"$/g, '');
                    localStorage.setItem("sessionId", sessionId);
                }
            } catch (e) { console.error("Помилка створення початкової сесії", e); }
        }

        // Завантажуємо повідомлення
        try {
            const msgRes = await fetch(`${API_BASE_URL}/sessions/${sessionId}`);
            if (msgRes.ok) {
                const sessionData = await msgRes.json();
                messagesContainer.innerHTML = ''; 
                
                if (sessionData.messages && sessionData.messages.length > 0) {
                    sessionData.messages.forEach(renderHistoryMessage);
                } else {
                    appendMessage(
                        "bot",
                        `Привіт, ${user.username || "студенте"}! Чим можу допомогти?`
                    );
                }
            }
        } catch (e) { 
            console.error("Помилка завантаження повідомлень", e);
            appendMessage("bot", "Помилка зв'язку з сервером."); 
        }
    }

    // ВІДПРАВКА ПОВІДОМЛЕННЯ (SSE-потік)
    let chatInFlight = false;

    async function sendMessage() {
        const text = userInput.value.trim();
        const sessionId = localStorage.getItem("sessionId");
        if (!text || !sessionId || chatInFlight) return;

        flushStreamingMathTimer();
        appendMessage("user", text);
        userInput.value = "";
        chatInFlight = true;
        if (sendBtn) sendBtn.disabled = true;
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
            const res = await fetch(`${API_BASE_URL}/faq/chat/stream`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Accept: "text/event-stream",
                },
                body: JSON.stringify({ message: text, sessionId, userId }),
            });

            if (!res.ok) {
                cleanupThinking();
                appendMessage(
                    "bot",
                    `Помилка сервера (${res.status}). Спробуйте ще раз.`
                );
                return;
            }

            await consumeSseStream(res, (ev) => {
                if (ev.type === "status" && ev.phase === "thinking") {
                    if (emotionLabel) emotionLabel.textContent = "думає…";
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
                    applyAvatarEmotion(ev);
                }
                if (ev.type === "error") {
                    streamBubble?.remove();
                    cleanupThinking();
                    appendMessage(
                        "bot",
                        ev.detail || "Помилка під час відповіді асистента."
                    );
                }
            });
        } catch (e) {
            console.error("Помилка відправки", e);
            streamBubble?.remove();
            cleanupThinking();
            appendMessage("bot", "Помилка відправки.");
        } finally {
            chatInFlight = false;
            if (sendBtn) sendBtn.disabled = false;
            if (userInput) userInput.disabled = false;
            if (emotionLabel && emotionLabel.textContent === "думає…") {
                emotionLabel.textContent = "Очікування";
            }
        }
    }

    // Події кнопок
    sendBtn?.addEventListener("click", sendMessage);
    userInput?.addEventListener("keypress", (e) => { 
        if (e.key === "Enter") {
            e.preventDefault();
            sendMessage();
        }
    });

    // === НОВИЙ ЧАТ (РЕАЛІЗАЦІЯ ЧЕРЕЗ newSession) ===
    newChatBtn?.addEventListener("click", async (e) => {
        e.preventDefault();
        try {
            // Звертаємось до твого спеціального ендпоінту
            const res = await fetch(`${API_BASE_URL}/sessions/newSession`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    userId: userId,
                    name: "new Чат з FAQ",
                    messages: [],
                    createdAt: new Date().toISOString(),
                    updatedAt: new Date().toISOString()
                }),
            });
            
            if (res.ok) {
                const rawId = await res.text();
                const newSessionId = rawId.replace(/^"|"$/g, '');
                
                // Оновлюємо ID в локальному сховищі
                localStorage.setItem("sessionId", newSessionId);
                
                // Очищаємо екран і завантажуємо "чисту" сесію
                messagesContainer.innerHTML = '';
                await initSession(); 
                console.log("Нову сесію створено успішно:", newSessionId);
            } else {
                console.error("Сервер не зміг створити нову сесію");
            }
        } catch (e) { 
            console.error("Помилка при створенні нового чату:", e); 
        }
    });

    // Запуск при завантаженні
    initSession();
});
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

    /** Markdown → безпечний HTML (якщо CDN недоступні — екранування). */
    function renderMarkdownToHtml(md) {
        const raw = md == null ? "" : String(md);
        if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
            const d = document.createElement("div");
            d.textContent = raw;
            return d.innerHTML.replace(/\n/g, "<br>");
        }
        const html = marked.parse(raw, { breaks: true, gfm: true });
        const clean = DOMPurify.sanitize(html);
        const wrap = document.createElement("div");
        wrap.innerHTML = clean;
        wrap.querySelectorAll("a[href]").forEach((a) => {
            a.setAttribute("target", "_blank");
            a.setAttribute("rel", "noopener noreferrer");
        });
        return wrap.innerHTML;
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
            inner.innerHTML = renderMarkdownToHtml(text);
            msgDiv.appendChild(inner);
        }
        messagesContainer.appendChild(msgDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    /** Повідомлення асистента: текст, опційно посилання (окремо від тексту). */
    function appendAssistantMessage(msg) {
        const wrap = document.createElement("div");
        wrap.className = "message bot";

        const body = document.createElement("div");
        body.className = "bot-message-body markdown-body";
        body.innerHTML = renderMarkdownToHtml(msg.text || "");
        wrap.appendChild(body);

        const linkVal = (msg.link || "").trim();
        if (isHttpUrl(linkVal)) {
            const linkRow = document.createElement("div");
            linkRow.className = "bot-message-link small mt-2";
            const a = document.createElement("a");
            a.href = linkVal;
            a.textContent = linkVal;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            a.className = "text-info";
            linkRow.appendChild(a);
            wrap.appendChild(linkRow);
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
                            streamBody.textContent = "…";
                        } else {
                            streamBody.innerHTML =
                                renderMarkdownToHtml(preview);
                        }
                    }
                    messagesContainer.scrollTop =
                        messagesContainer.scrollHeight;
                }
                if (ev.type === "done") {
                    cleanupThinking();
                    streamBubble?.remove();
                    streamBubble = null;
                    streamBody = null;
                    appendAssistantMessage({
                        text: ev.response || "",
                        link: ev.link || "",
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
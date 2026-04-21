window.addEventListener("DOMContentLoaded", async () => {
    const API_BASE_URL = "/api";
    
    // Елементи DOM
    const sidebar = document.getElementById("sidebar");
    const mainContent = document.getElementById("main-content");
    const toggleBtn = document.getElementById("sidebar-toggle");
    const messagesContainer = document.getElementById("messages");
    const userInput = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");
    const attachBtn = document.getElementById("attach-btn");
    const fileInput = document.getElementById("file-input");
    const avatarVideo = document.getElementById("avatar-video");
    const emotionLabel = document.getElementById("emotion-status");
    const newChatBtn = document.getElementById("new-chat-btn");
    const avatarBox = document.querySelector('.avatar-fixed');
    const resizeHandle = document.querySelector('.resize-handle');
    const themeToggle = document.getElementById("theme-toggle");
    const confirmLogout = document.getElementById("confirmLogout");

    const user = JSON.parse(localStorage.getItem("user"));
    const userId = user?._id?.$oid || user?._id || null;

    // --- 1. ТЕМА ---
    function applyTheme(t) {
        document.body.setAttribute("data-theme", t); localStorage.setItem("theme", t);
        const i = document.getElementById("theme-icon"); if (i) i.className = t === "light" ? "bi bi-sun-fill" : "bi bi-moon-stars-fill";
    }
    applyTheme(localStorage.getItem("theme") || "dark");
    themeToggle?.addEventListener("click", () => applyTheme(document.body.getAttribute("data-theme") === "dark" ? "light" : "dark"));

    // --- 2. ВИХІД ---
    confirmLogout?.addEventListener("click", () => { localStorage.clear(); window.location.href = "/"; });

    // --- 3. ЗГОРТАННЯ МЕНЮ ---
    toggleBtn?.addEventListener("click", () => {
        sidebar.classList.toggle("collapsed");
        mainContent.classList.toggle("expanded");
    });

    // --- 4. ПРИКРІПЛЕННЯ ---
    attachBtn?.addEventListener("click", () => fileInput.click());
    fileInput?.addEventListener("change", (e) => {
        if(e.target.files[0]) appendMessage("bot", `📁 Файл: ${e.target.files[0].name}`);
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

    // --- 6. ЛОГІКА ЧАТУ (Тільки якщо є messagesContainer) ---
    function appendMessage(role, text) {
        if (!messagesContainer) return;
        const d = document.createElement("div"); d.className = `message ${role === "user" ? "user" : "bot"}`;
        d.textContent = text; messagesContainer.appendChild(d); messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    async function initSession() {
        if (!messagesContainer || !userId) return;
        let sid = localStorage.getItem("sessionId");
        if (!sid || sid === "null") {
            try {
                const res = await fetch(`${API_BASE_URL}/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId, name: "Новий чат", messages: [], createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() }) });
                if (res.ok) { const id = await res.text(); sid = id.replace(/^"|"$/g, ''); localStorage.setItem("sessionId", sid); }
            } catch (e) { console.error(e); }
        }
        try {
            const res = await fetch(`${API_BASE_URL}/sessions/${sid}`);
            if (res.ok) {
                const data = await res.json(); messagesContainer.innerHTML = ''; 
                if (data.messages.length > 0) data.messages.forEach(m => appendMessage(m.role === "user" ? "user" : "bot", m.text));
                else appendMessage("bot", "Привіт! Чим можу допомогти?");
            }
        } catch (e) { console.error(e); }
    }

    async function sendMessage() {
        const text = userInput.value.trim(), sid = localStorage.getItem("sessionId");
        if (!text || !sid) return;
        appendMessage("user", text); userInput.value = ""; userInput.disabled = true;
        try {
            const res = await fetch(`${API_BASE_URL}/faq/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, sessionId: sid, userId }) });
            const d = await res.json(); 
            if (avatarBox) { avatarBox.classList.add('active-glow'); setTimeout(() => avatarBox.classList.remove('active-glow'), 4000); }
            appendMessage("bot", d.response);
        } catch (e) { appendMessage("bot", "Помилка."); }
        finally { userInput.disabled = false; userInput.focus(); }
    }

    sendBtn?.addEventListener("click", sendMessage);
    userInput?.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
    
    newChatBtn?.addEventListener("click", async (e) => {
        if (!userId) { window.location.href = "/auth"; return; }
        e.preventDefault();
        try {
            const res = await fetch(`${API_BASE_URL}/sessions/newSession`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId, name: "Новий чат", messages: [], createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() }) });
            if (res.ok) { const id = await res.text(); localStorage.setItem("sessionId", id.replace(/^"|"$/g, '')); window.location.href = "/chat"; }
        } catch (e) { console.error(e); }
    });

    if (window.location.pathname.includes('chat')) initSession();
});
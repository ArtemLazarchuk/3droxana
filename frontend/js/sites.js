const API_URL = "/api/links"; // відносний шлях для роботи через Docker/IP

const grid = document.getElementById("sitesGrid");
const emptyMessage = document.getElementById("emptyMessage");

// Функція створення HTML-картки
function createCard(link) {
  const card = document.createElement("div");
  card.className = "site-card";

  // Перевіряємо, чи URL починається з http:// або https://
  let url = link.url;
  if (!/^https?:\/\//i.test(url)) {
    url = "https://" + url;
  }

  card.innerHTML = `
    <h2>${link.title}</h2>
    <p>${link.description || "Без опису"}</p>
    <a href="${url}" target="_blank" rel="noopener noreferrer">Відвідати</a>
  `;

  return card;
}

// Завантаження даних з API
async function loadLinks() {
  try {
    const response = await fetch(API_URL);
    if (!response.ok) throw new Error("Помилка завантаження посилань");

    const links = await response.json();
    grid.innerHTML = "";

    const visibleLinks = links.filter((link) => link.visible);

    if (visibleLinks.length === 0) {
      emptyMessage.style.display = "block";
      return;
    }

    visibleLinks.forEach((link) => {
      const card = createCard(link);
      grid.appendChild(card);
    });
  } catch (error) {
    console.error(error);
    emptyMessage.style.display = "block";
    emptyMessage.textContent = "Помилка під час завантаження посилань.";
  }
}

// Запуск при завантаженні сторінки
document.addEventListener("DOMContentLoaded", loadLinks);

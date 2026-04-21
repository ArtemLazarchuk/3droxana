## 3droxana — AI‑чат для студентів

3droxana — це веб‑застосунок з FastAPI‑бекендом і статичним фронтендом, який надає **розумного чат‑асистента для студентів**:

- **Пояснює правила та процеси навчання** (предмети, реєстрація, оцінювання, стипендії тощо).
- **Підказує корисні ресурси** з бази посилань коледжу / університету.
- **Веде історію сесій спілкування**, щоб можна було повертатись до попередніх діалогів.

Бекенд працює поверх FastAPI, зберігає дані у MongoDB і викликає зовнішню LLM‑модель (через Together API).

---

## Вимоги

- Python 3.12+
- **Windows:** Git Bash, PowerShell або CMD (для шляхів і активації venv нижче — різниця між оболонками)
- **macOS:** стандартний Terminal (zsh)
- Доступ до MongoDB (локальний або MongoDB Atlas)

---

## Локальний запуск (venv)

### 1. Клонувати репозиторій та перейти в папку проєкту

Однаково на **Windows** і **macOS**:

```bash
git clone <URL-репозиторію>
cd 3droxana
```

### 2. Створити віртуальне середовище (один раз)

| Платформа | Команда |
|-----------|---------|
| **Windows** | `py -3 -m venv venv` — якщо встановлений **Python Launcher** (`py`). Якщо `py` не знаходиться, використайте `python -m venv venv` або повний шлях до `python.exe` з інсталятора python.org. |
| **macOS** | `python3 -m venv venv` — на Apple зазвичай **немає** команди `py`; використовуйте `python3`. |

### 3. Активувати venv

| Платформа | Оболонка | Команда |
|-----------|----------|---------|
| **Windows** | Git Bash | `source venv/Scripts/activate` |
| **Windows** | CMD | `venv\Scripts\activate.bat` |
| **Windows** | PowerShell | `venv\Scripts\Activate.ps1` |
| **macOS** | zsh / bash | `source venv/bin/activate` |

У рядку термінала має з’явитися префікс `(venv)`.

### 4. Встановити залежності

Після активації venv на **обох** платформах надійний варіант — через модуль `pip` (так працює навіть якщо команда `pip` не в `PATH`):

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

На macOS, якщо venv ще **не** активовано, можна одноразово викликати: `python3 -m pip install -r requirements.txt` — але краще спочатку активувати venv, щоб пакети ставили саме в нього.

### 5. Налаштувати секрети через `.env` (Можна звернутись до розробників по секрети)

У корені проєкту створіть файл `.env` (він уже доданий у `.gitignore`, тому не потрапить у git) з таким вмістом **(значення замініть на свої)**:


```env
MONGODB_URI="mongodb://localhost:27017"
DATABASE_NAME="3davatar"
TOGETHER_API_KEY="ваш-ключ-Together"

GOOGLE_CLIENT_ID=""
GOOGLE_CLIENT_SECRET=""
```

- `MONGODB_URI` — повний URI до вашого MongoDB/Atlas‑кластера.
- `DATABASE_NAME` — назва бази, за замовчуванням використовується `3davatar`.
- `TOGETHER_API_KEY` — API‑ключ від Together (для LLM‑моделі).

### 6. Запустити сервер

Однаково після активації venv:

```bash
python -m uvicorn backend.main:app --reload --port 8000
```

Після запуску API та фронтенд будуть доступні за адресою:

- `http://localhost:8000` — основна сторінка застосунку.

---

## Запуск через Docker

### 1. Збірка образу

```bash
docker build -t 3droxana .
```

### 2. Запуск контейнера з секретами

Рекомендовано передавати ті ж самі змінні оточення, що й у `.env`:

```bash
docker run -p 8000:8000 \
  -e MONGODB_URI="mongodb://..." \
  -e DATABASE_NAME="3davatar" \
  -e TOGETHER_API_KEY="ваш-ключ" \
  -e JWT_SECRET_KEY="випадковий-рядок" \
  -e GOOGLE_CLIENT_ID="" \
  -e GOOGLE_CLIENT_SECRET="" \
  3droxana
```

Після цього застосунок буде доступний на `http://localhost:8000`.

---

## Коротко про структуру

- `backend/` — FastAPI‑бекенд (роути, моделі, робота з MongoDB).
- `assistant_core/` — бізнес‑логіка чат‑асистента (виклики LLM, парсинг відповіді, оновлення сесій).
- `frontend/` — статичні HTML/CSS/JS‑сторінки для інтерфейсу.
- `avatar/` — медіафайли для 3D‑/анімаційного аватара.
- `requirements.txt` — список Python‑залежностей.
- `Dockerfile` — опис Docker‑образу для продакшен/контейнерного запуску.
- `.env` — **локальні секрети** (не комітяться в git).

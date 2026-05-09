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
PUBLIC_APP_BASE_URL="http://127.0.0.1:6060"
MAIL_USERNAME=""
MAIL_PASSWORD=""
MAIL_FROM=""
```

- `MONGODB_URI` — повний URI до вашого MongoDB/Atlas‑кластера.
- `DATABASE_NAME` — назва бази, за замовчуванням використовується `3davatar`.
- `TOGETHER_API_KEY` — API‑ключ від Together (для LLM‑моделі).
- `PUBLIC_APP_BASE_URL` — базова URL застосунку без завершального слеша (наприклад `https://your-domain.com` на сервері).
- `MAIL_USERNAME`, `MAIL_PASSWORD` — облікові дані SMTP; без них функції надсилання пошти не працюватимуть.
- `MAIL_FROM` — адреса «Від кого» у листах; якщо порожньо, зазвичай використовується `MAIL_USERNAME`.

Опційно для пошти (якщо не Gmail): у `backend/config.py` також читаються `MAIL_SERVER` (за замовчуванням `smtp.gmail.com`) та `MAIL_PORT` (`587`).

### 6. Запустити сервер

Однаково після активації venv:

```bash
python -m uvicorn backend.main:app --reload --port 6060
```

Після запуску API та фронтенд будуть доступні за адресою:

- `http://localhost:6060` — основна сторінка застосунку.

---

## Запуск через Docker

### 1. Збірка образу

```bash
docker build -t 3droxana .
```

### 2. Запуск контейнера з секретами

Файл `.env` **не копіюється в образ** (тільки локально / на сервері). Варіанти:

1. **Рекомендовано** — підставити змінні з вашого `.env`:

```bash
docker run -p 6060:6060 --env-file .env 3droxana
```

2. Змонтувати `.env` у `/app` (тоді `python-dotenv` прочитає його при старті):

```bash
docker run -p 6060:6060 -v "$(pwd)/.env:/app/.env:ro" 3droxana
```

3. Якщо потрібен лише перелік змінних — використовуйте `-e` (як нижче).

Штатна **ML-модель** (`models/emotion_model.joblib`) входить у репозиторій і потрапляє в Docker-образ. Щоб підмінити її своєю після повторного тренування:

```bash
docker run -p 6060:6060 --env-file .env \
  -v "$(pwd)/models/emotion_model.joblib:/app/models/emotion_model.joblib:ro" \
  3droxana
```

Альтернатива — явно передати змінні вручну:

```bash
docker run -p 6060:6060 \
  -e MONGODB_URI="mongodb://..." \
  -e DATABASE_NAME="3davatar" \
  -e TOGETHER_API_KEY="ваш-ключ" \
  -e JWT_SECRET_KEY="випадковий-рядок" \
  -e GOOGLE_CLIENT_ID="" \
  -e GOOGLE_CLIENT_SECRET="" \
  -e PUBLIC_APP_BASE_URL="https://example.com" \
  -e MAIL_USERNAME="" \
  -e MAIL_PASSWORD="" \
  -e MAIL_FROM="" \
  3droxana
```

Після цього застосунок буде доступний на `http://localhost:6060`.

---

## Коротко про структуру

- `backend/` — FastAPI‑бекенд (роути, моделі, робота з MongoDB).
- `assistant_core/` — бізнес‑логіка чат‑асистента (виклики LLM, парсинг відповіді, оновлення сесій).
- `frontend/` — статичні HTML/CSS/JS‑сторінки для інтерфейсу.
- `avatar/` — медіафайли для 3D‑/анімаційного аватара.
- `models/emotion_model.joblib` — ML-класифікатор емоцій (можна перетренувати: `python -m scripts.train_emotion_model` у venv).
- `Dockerfile` — опис Docker‑образу для продакшен/контейнерного запуску.
- `.env` — **локальні секрети** (не комітяться в git).

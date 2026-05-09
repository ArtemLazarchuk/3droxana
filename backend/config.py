import os

MONGODB_URI = os.environ.get("MONGODB_URI")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "3davatar")

if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI is not set. Configure it via environment variables or .env file."
    )

# JWT / auth settings
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-me")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440")  # 24 години за замовчуванням
)

# Опційно: вхід через Google OAuth (див. README)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
# Якщо застосунок за reverse-proxy і request.base_url дає http://internal — задайте публічний базовий URL без слеша в кінці.
PUBLIC_APP_BASE_URL = os.environ.get("PUBLIC_APP_BASE_URL", "").strip().rstrip("/")

# Email (Gmail SMTP) для скидання пароля
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "").strip()
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "").strip()
MAIL_FROM    = os.environ.get("MAIL_FROM", MAIL_USERNAME).strip()
MAIL_SERVER  = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT    = int(os.environ.get("MAIL_PORT", "587"))

# Час життя токена скидання пароля (хвилини)
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 15

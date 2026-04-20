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

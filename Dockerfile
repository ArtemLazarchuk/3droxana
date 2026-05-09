# 3droxana — FastAPI backend + static frontend
FROM python:3.12-slim

WORKDIR /app

# Для scipy / scikit-learn (wheel) на slim-образі
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Project code (backend, assistant logic, frontend, avatar, shipped ML model)
COPY backend ./backend
COPY assistant_core ./assistant_core
COPY frontend ./frontend
COPY avatar ./avatar

# Port
EXPOSE 6060

# MONGODB_URI and DATABASE_NAME can be passed via -e in docker run
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "6060"]

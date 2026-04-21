import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

from backend.api import routes_faq
from backend.api import routes_feedback
from backend.api import routes_links
from backend.api import routes_sessions
from backend.api import routes_users
from backend.db.mongodb import connect_to_mongo, close_mongo_connection


load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # або ["*"] для всіх
    allow_credentials=True,
    allow_methods=["*"],  # або ['POST', 'GET'] тощо
    allow_headers=["*"],
)
# Подати папку frontend/ як статичну
app.mount("/static", StaticFiles(directory="frontend"), name="static")
# Подати папку avatar/ як статичну
app.mount("/avatar", StaticFiles(directory="avatar"), name="avatar")

_NO_STORE_HTML = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@app.get("/", response_class=FileResponse)
async def serve_frontend():
    print("Serving index.html...")
    return FileResponse(
        os.path.join("frontend", "pages", "index.html"), headers=_NO_STORE_HTML
    )


@app.get("/auth", response_class=FileResponse)
async def serve_auth():
    return FileResponse(os.path.join("frontend", "pages", "auth.html"), headers=_NO_STORE_HTML)


@app.get("/chat", response_class=FileResponse)
async def serve_faq():
    return FileResponse(os.path.join("frontend", "pages", "chat.html"), headers=_NO_STORE_HTML)


@app.get("/history", response_class=FileResponse)
async def serve_faq():
    return FileResponse(os.path.join("frontend", "pages", "history.html"), headers=_NO_STORE_HTML)


@app.get("/sites", response_class=FileResponse)
async def serve_faq():
    return FileResponse(os.path.join("frontend", "pages", "sites.html"), headers=_NO_STORE_HTML)

app.include_router(routes_faq.router)
app.include_router(routes_feedback.router)
app.include_router(routes_links.router)
app.include_router(routes_sessions.router)
app.include_router(routes_users.router)
@app.on_event("startup")
async def startup_db():
    await connect_to_mongo()

@app.on_event("shutdown")
async def shutdown_db():
    await close_mongo_connection()

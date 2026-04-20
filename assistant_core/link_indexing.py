"""
Завантаження тексту з URL посилань, чанки в MongoDB, контекст для чату (RAG).
Індексація: після POST /api/links/ та один раз «ліниво» при першому повідомленні в чаті.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

import httpx
import trafilatura
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

CHUNKS_COLLECTION = "link_chunks"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
HTTP_TIMEOUT = 30.0
MAX_CONTEXT_CHARS = 28_000


def _split_into_chunks(text: str, max_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_size, n)
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _tokenize(s: str) -> Set[str]:
    return set(re.findall(r"[\w\u0400-\u04FF]{2,}", (s or "").lower()))


def _score_chunk(user_message: str, chunk_text: str) -> int:
    ut = _tokenize(user_message)
    if len(ut) < 1:
        return 0
    return len(ut & _tokenize(chunk_text))


async def index_link_content(
    db: AsyncIOMotorDatabase,
    link_id: str,
    url: str,
) -> int:
    """
    Знімає HTML, витягує текст, перезаписує чанки для link_id.
    Повертає кількість збережених чанків.
    """
    err_msg: str | None = None
    chunks_texts: List[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "3droxana-link-indexer/1.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        extracted = await asyncio.to_thread(
            trafilatura.extract,
            html,
            url=url,
            include_comments=False,
            include_tables=True,
        )
        raw = (extracted or "").strip()
        chunks_texts = _split_into_chunks(raw) if raw else []
    except Exception as exc:  # noqa: BLE001
        logger.exception("index_link_content failed for %s: %s", url, exc)
        err_msg = str(exc)[:500]

    await db[CHUNKS_COLLECTION].delete_many({"link_id": link_id})

    if chunks_texts:
        now = datetime.now(timezone.utc)
        docs = [
            {
                "link_id": link_id,
                "chunk_index": i,
                "text": t,
                "url": url,
                "fetched_at": now,
            }
            for i, t in enumerate(chunks_texts)
        ]
        try:
            await db[CHUNKS_COLLECTION].insert_many(docs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("insert_many link_chunks failed for %s", link_id)
            err_msg = (err_msg + "; " if err_msg else "") + str(exc)[:300]
            chunks_texts = []

    await db["links"].update_one(
        {"_id": ObjectId(link_id)},
        {
            "$set": {
                "web_index_tried_at": datetime.now(timezone.utc),
                "web_index_error": err_msg,
            }
        },
    )
    n = len(chunks_texts)
    if err_msg:
        logger.warning("link_index: link_id=%s chunks=%s error=%s", link_id, n, err_msg[:200])
    else:
        logger.info("link_index: link_id=%s chunks=%s url=%s", link_id, n, url[:120])
    return n


async def ensure_link_indexed(db: AsyncIOMotorDatabase, link: Dict[str, Any]) -> None:
    """Індексує видиме посилання, якщо ще немає чанків і не було спроби."""
    if not link.get("visible", True):
        return
    link_id = str(link["_id"])
    n = await db[CHUNKS_COLLECTION].count_documents({"link_id": link_id})
    if n > 0:
        return
    if link.get("web_index_tried_at") is not None:
        return
    url = link.get("url") or ""
    if not url.startswith(("http://", "https://")):
        await db["links"].update_one(
            {"_id": ObjectId(link_id)},
            {
                "$set": {
                    "web_index_tried_at": datetime.now(timezone.utc),
                    "web_index_error": "Некоректний URL (потрібен http/https)",
                }
            },
        )
        return
    await index_link_content(db, link_id, url)


def _metadata_fallback(links: List[Dict[str, Any]]) -> str:
    return "\n\n".join(
        f"{i + 1}) Назва: {link['title']}\n"
        f"Опис: {link['description']}\n"
        f"Теги: {', '.join(link.get('tags', []))}\n"
        f"Посилання: {link['url']}"
        for i, link in enumerate(links)
    )


async def build_rag_context(
    db: AsyncIOMotorDatabase,
    user_message: str,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """
    Лінива індексація видимих посилань, далі уривки з link_chunks за релевантністю до запиту.
    """
    raw = await db["links"].find({}).to_list(None)
    links = [l for l in raw if l.get("visible", True)]
    if not links:
        return "(Немає видимих посилань у базі.)"

    for link in links:
        await ensure_link_indexed(db, link)

    link_by_id = {str(link["_id"]): link for link in links}
    link_ids = list(link_by_id.keys())
    all_chunks = await db[CHUNKS_COLLECTION].find({"link_id": {"$in": link_ids}}).to_list(None)

    if not all_chunks:
        return _metadata_fallback(links)

    scored: List[tuple[int, Dict[str, Any]]] = []
    for ch in all_chunks:
        score = _score_chunk(user_message, ch.get("text", ""))
        scored.append((score, ch))

    scored.sort(
        key=lambda x: (-x[0], x[1]["link_id"], x[1].get("chunk_index", 0)),
    )

    parts: List[str] = []
    used = 0
    last_link_id: str | None = None

    for _score, ch in scored:
        lid = ch["link_id"]
        text = (ch.get("text") or "").strip()
        if not text:
            continue

        link = link_by_id.get(lid)
        prefix = ""
        if link and lid != last_link_id:
            prefix = (
                f"\n### {link['title']}\n"
                f"URL: {link['url']}\n"
                f"Теги: {', '.join(link.get('tags', []))}\n"
                f"Опис (картка): {link.get('description', '')}\n\n"
            )
            last_link_id = lid

        piece = prefix + text
        if used + len(piece) > max_chars:
            room = max_chars - used - len(prefix)
            if room < 120:
                break
            piece = prefix + text[:room]
        parts.append(piece)
        used += len(piece)
        if used >= max_chars:
            break

    if not parts:
        return _metadata_fallback(links)

    intro = (
        "Нижче — уривки зі сторінок (індексовані фрагменти). Відповідай лише на їх основі для фактів; "
        "URL для поля «посилання» бери з заголовків джерел.\n\n"
    )
    return intro + "\n".join(parts)

"""
Завантаження тексту з URL посилань, чанки в MongoDB, контекст для чату (RAG).
Також у контекст входять записи з колекції faq (питання–відповідь).
Індексація посилань: після POST /api/links/ та ліниво при першому зверненні до чату.
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
FAQ_COLLECTION = "faq"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
HTTP_TIMEOUT = 30.0
MAX_CONTEXT_CHARS = 28_000
MAX_CATALOG_CHARS = 9000
MAX_FAQ_CHARS = 10_000
MIN_CHUNK_BUDGET = 3500


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
    """Слова (≥2 символи) + послідовності цифр («7», «11» тощо)."""
    s_low = (s or "").lower()
    words = set(re.findall(r"[\w\u0400-\u04FF]{2,}", s_low))
    nums = set(re.findall(r"\d+", s_low))
    return words | nums


def _score_chunk(
    user_message: str,
    chunk_text: str,
    link: Dict[str, Any] | None = None,
) -> int:
    ut = _tokenize(user_message)
    if len(ut) < 1:
        return 0
    ct = _tokenize(chunk_text)
    score = len(ut & ct)
    if link:
        meta = " ".join(
            [
                str(link.get("title", "")),
                str(link.get("description", "")),
                str(link.get("url", "")),
                " ".join(link.get("tags", []) or []),
            ]
        ).lower()
        score += len(ut & _tokenize(meta))
    return score


def _score_faq(user_message: str, faq: Dict[str, Any]) -> int:
    blob = " ".join(
        [
            str(faq.get("question", "")),
            str(faq.get("answer", "")),
            " ".join(faq.get("keywords") or []),
            " ".join(faq.get("tags") or []),
            str(faq.get("source") or ""),
            str(faq.get("category", "")),
            str(faq.get("subcategory", "")),
        ]
    )
    ut = _tokenize(user_message)
    if not ut:
        return 0
    return len(ut & _tokenize(blob))


def _format_faq_block(faq: Dict[str, Any], index: int) -> str:
    kw = faq.get("keywords") or []
    tags = faq.get("tags") or []
    src = faq.get("source") or ""
    return (
        f"{index}) Питання: {faq.get('question', '')}\n"
        f"Відповідь: {faq.get('answer', '')}\n"
        f"Ключові слова: {', '.join(kw)}\n"
        f"Теги: {', '.join(tags)}\n"
        f"Джерело: {src}\n"
        f"Категорія: {faq.get('category', '')} / {faq.get('subcategory', '')}\n"
    )


async def _build_faq_context(
    db: AsyncIOMotorDatabase,
    user_message: str,
    max_chars: int,
) -> str:
    """Текстовий блок з видимих FAQ, відсортованих за релевантністю до запиту."""
    raw = await db[FAQ_COLLECTION].find({}).to_list(None)
    faqs = [f for f in raw if f.get("visible", True)]
    if not faqs:
        return ""
    scored = [(_score_faq(user_message, f), f) for f in faqs]
    scored.sort(key=lambda x: (-x[0], str(x[1].get("question", ""))))
    parts: List[str] = []
    used = 0
    idx = 1
    for _sc, faq in scored:
        block = _format_faq_block(faq, idx)
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
        idx += 1
    if not parts:
        return ""
    return (
        "БАЗА FAQ (готові відповіді з бази; використовуй для фактів. "
        "URL у полі «посилання» можна брати з «Джерело», якщо це http(s):\n\n"
        + "\n".join(parts)
    )


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


def _link_catalog_block(links: List[Dict[str, Any]], max_chars: int) -> str:
    parts: List[str] = []
    used = 0
    for i, link in enumerate(links):
        block = (
            f"{i + 1}) Назва: {link.get('title', '')}\n"
            f"Опис: {link.get('description', '')}\n"
            f"Теги: {', '.join(link.get('tags', []) or [])}\n"
            f"Посилання: {link.get('url', '')}\n"
        )
        if used + len(block) > max_chars:
            parts.append(f"\n[… ще {len(links) - len(parts)} посилань …]\n")
            break
        parts.append(block)
        used += len(block)
    return (
        "КАТАЛОГ ПОСИЛАНЬ (видимі записи з колекції links; URL для «посилання»):\n\n"
        + "\n".join(parts)
    )


async def build_rag_context(
    db: AsyncIOMotorDatabase,
    user_message: str,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """
    Контекст для LLM: FAQ + каталог посилань + релевантні чанки зі сторінок.
    """
    faq_block = await _build_faq_context(db, user_message, MAX_FAQ_CHARS)
    faq_len = len(faq_block)

    raw = await db["links"].find({}).to_list(None)
    links = [l for l in raw if l.get("visible", True)]

    if not links:
        if faq_block:
            return faq_block
        return "(Немає даних у базі: ні посилань, ні FAQ.)"

    for link in links:
        await ensure_link_indexed(db, link)

    link_by_id = {str(link["_id"]): link for link in links}
    link_ids = list(link_by_id.keys())
    all_chunks = await db[CHUNKS_COLLECTION].find({"link_id": {"$in": link_ids}}).to_list(None)

    reserved = faq_len + 400
    cat_budget = min(MAX_CATALOG_CHARS, max(2000, (max_chars - reserved) // 2))
    catalog = _link_catalog_block(links, cat_budget)
    cat_len = len(catalog)

    chunk_budget = max(MIN_CHUNK_BUDGET, max_chars - faq_len - cat_len - 350)

    chunk_parts: List[str] = []
    if all_chunks:
        scored: List[tuple[int, Dict[str, Any]]] = []
        for ch in all_chunks:
            lid = ch["link_id"]
            link = link_by_id.get(lid)
            score = _score_chunk(user_message, ch.get("text", ""), link)
            scored.append((score, ch))

        scored.sort(
            key=lambda x: (-x[0], x[1]["link_id"], x[1].get("chunk_index", 0)),
        )

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
            if used + len(piece) > chunk_budget:
                room = chunk_budget - used - len(prefix)
                if room < 120:
                    break
                piece = prefix + text[:room]
            chunk_parts.append(piece)
            used += len(piece)
            if used >= chunk_budget:
                break

    chunks_body = ""
    if chunk_parts:
        chunks_body = (
            "УРИВКИ ЗІ СТОРІНОК (індексований текст сайтів; доповнюють FAQ і каталог):\n\n"
            + "\n".join(chunk_parts)
        )
    elif links:
        chunks_body = "Уривків зі сторінок немає; використовуй FAQ і каталог посилань.\n"

    sections: List[str] = []
    if faq_block:
        sections.append(faq_block)
    sections.append(catalog)
    if chunks_body:
        sections.append(chunks_body)

    return "\n\n---\n\n".join(sections)

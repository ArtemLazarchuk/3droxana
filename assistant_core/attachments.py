"""
Вкладення в чат: PDF (локальний текст, без додаткового API) та зображення (vision через Together).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from typing import Tuple

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PDF_TEXT_CHARS = 24_000

# Модель з підтримкою зображень; рахується окремо від основного чату (токени vision).
DEFAULT_VISION_MODEL = "Qwen/Qwen2-VL-7B-Instruct"


def _is_pdf(filename: str, content: bytes) -> bool:
    fn = (filename or "").lower()
    if fn.endswith(".pdf"):
        return True
    return len(content) >= 5 and content[:5] == b"%PDF-"


def _is_image_filename(filename: str) -> bool:
    fn = (filename or "").lower()
    return fn.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _image_mime(filename: str) -> str:
    fn = (filename or "").lower()
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".webp"):
        return "image/webp"
    if fn.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _extract_pdf_text(content: bytes) -> Tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "Не встановлено pypdf (pip install pypdf)."
    try:
        reader = PdfReader(io.BytesIO(content))
        parts: list[str] = []
        for i, page in enumerate(reader.pages):
            if i >= 80:
                parts.append("\n[… подальші сторінки пропущені …]")
                break
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("PDF read failed")
        return "", f"Не вдалося прочитати PDF: {exc}"
    if not text:
        return "", "У PDF не знайдено тексту (можливо скан — потрібен OCR або зображення через vision)."
    if len(text) > MAX_PDF_TEXT_CHARS:
        text = text[: MAX_PDF_TEXT_CHARS - 80] + "\n\n[… текст обрізано …]"
    return text, ""


def _maybe_resize_image(content: bytes, mime: str) -> Tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError:
        return content, mime
    try:
        im = Image.open(io.BytesIO(content))
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        w, h = im.size
        max_side = 1280
        if max(w, h) > max_side:
            ratio = max_side / float(max(w, h))
            im = im.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception as exc:  # noqa: BLE001
        logger.warning("PIL resize skipped: %s", exc)
        return content, mime


def _describe_image_sync(image_bytes: bytes, mime: str) -> Tuple[str, str]:
    api_key = os.environ.get("TOGETHER_API_KEY")
    if not api_key:
        return "", "TOGETHER_API_KEY не задано."
    image_bytes, mime = _maybe_resize_image(image_bytes, mime)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    model = os.environ.get("TOGETHER_VISION_MODEL", DEFAULT_VISION_MODEL).strip()
    try:
        from together import Together

        client = Together(api_key=api_key)
        prompt = (
            "Опиши зображення або скріншот українською максимально детально для студента: "
            "усі видимі написи й числа дослівно, таблиці структуровано, формули — у LaTeX у \\( \\) або $$, "
            "схеми коротко по кроках. Якщо це задача з підручника — переформулюй умову чітко."
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return "", "Vision-модель повернула порожній текст."
        return text, ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("Vision call failed model=%s", model)
        return "", f"Помилка vision API: {exc!s}"[:500]


async def prepare_user_attachment(filename: str, content: bytes) -> Tuple[str, str]:
    """
    Готує блок тексту для LLM (українською в преамбулі).
    Повертає (attachment_block, error). error порожній при успіху.
    """
    fn = filename or "file"
    if _is_pdf(fn, content):
        if len(content) > MAX_PDF_BYTES:
            return "", "PDF завеликий (макс. 12 МБ)."
        text, err = await asyncio.to_thread(_extract_pdf_text, content)
        if err:
            return "", err
        block = f"Вкладення «{fn}» (PDF, витягнутий текст):\n\n{text}"
        return block, ""

    if not _is_image_filename(fn):
        return "", "Підтримуються PDF або зображення PNG, JPG, WEBP, GIF."

    if len(content) > MAX_IMAGE_BYTES:
        return "", "Зображення завелике (макс. 8 МБ)."

    mime = _image_mime(fn)
    desc, err = await asyncio.to_thread(_describe_image_sync, content, mime)
    if err:
        return "", err
    block = f"Вкладення «{fn}» (опис зображення з vision-моделі):\n\n{desc}"
    return block, ""

import asyncio
import json
import os
import re
import threading
import traceback
from datetime import datetime
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional, Union

from bson import ObjectId
from dotenv import load_dotenv
from together import Together

from assistant_core.link_indexing import build_rag_context
from assistant_core.emotion_engine import (
    analyze_emotion,
    get_avatar_controller,
    reset_session_context,
    select_animation_for_emotion_label,
)


class ChatSessionNotFound(Exception):
    """Сесію з таким id не знайдено."""


class ChatServiceError(Exception):
    """Загальна помилка сервісу чату."""


load_dotenv()

TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY")
if not TOGETHER_API_KEY:
    raise RuntimeError(
        "TOGETHER_API_KEY is not set. Configure it via environment variables or .env file."
    )

client = Together(api_key=TOGETHER_API_KEY)

# Мітки емоцій для аватара (англ.); збігайте з `videoMap` у frontend/js/chat.js
ALLOWED_EMOTIONS: FrozenSet[str] = frozenset(
    {"neutral", "happy", "sad", "surprise", "thinking", "angry", "disgust"}
)

# Назва сесії в списку «Історія»; поки лишається так — підставляємо тему з першої відповіді моделі.
_SESSION_NAME_DEFAULTS: FrozenSet[str] = frozenset({"Новий чат", "Без назви", ""})
_SESSION_NAME_MAX_LEN = 100

# Спочатку «основний текст» — щоб при streaming користувач бачив відповідь раніше за службові поля.
SYSTEM_PROMPT = (
    "Ти — чат-асистент для студентів КПІ: спокійний, чемний, по суті. "
    "Тон дружній, але без зайвої панібратської розмовності. "
    "Емодзі та смайлики в полях «основний текст» і «текст чату» не використовуй; якщо дуже доречно — не більше одного на все повідомлення. "
    "Не додавай рядки на кшталт «вау!», «ого!» лише заради ефекту. "
    "Якщо користувач ставить офіційне запитання щодо навчання чи КПІ, дай чітку, інформативну відповідь. "
    "Для таких відповідей використовуй лише ту інформацію, яка є в контексті (FAQ, посилання з бази, уривки зі сторінок). "
    "Посилання ти можеш використовувати тільки з контексту. "
    "Не вигадуй нові посилання."
    "Якщо користувач просто хоче поспілкуватися, підтримай розмову стримано й доброзичливо; якщо це не факти з контексту — вкажи, що це твоя особиста думка. "
    "ВАЖЛИВО: ти ЗАВЖДИ надаєш відповідь тільки в одному строго визначеному форматі. "
    "Не додаєш жодного слова, жодного речення, жодного пояснення за межами шаблону. Не ігноруєш структуру. "
    "Порядок полів ЗАВЖДИ такий (спочатку основний текст — для зручності читання):\n"
    "основний текст: {розгорнута відповідь у Markdown (GFM, як на GitHub): **жирний**, списки, абзаци. "
    "Для порівнянь, критеріїв, правил, стипендій тощо **за замовчуванням використовуй GFM-таблиці** (рядки з |), а не лише списки. "
    "Перед кожною таблицею — 1–2 речення, що вона показує; за потреби після таблиці — короткий висновок. Не залишай «голу» таблицю без пояснення. "
    "Математику й формули оформлюй у **LaTeX**: у рядку через \\( ... \\), блочно через $$ ... $$ або **зворотний сліш** + дужки \\[ ... \\]. "
    "Блочне $$ ... $$ пиши в один рядок без порожніх рядків усередині; змінні після формули краще як \\(I\\), \\(q\\), а не ( I ). "
    "Ніколи не обгортай формулу лише «голими» квадратними дужками [ ] на окремих рядках без \\ — так воно не відрендериться. "
    "Уникай «сирих» символів замість формули, якщо доречніше LaTeX. Якщо поруч таблиця й формула — одне коротке речення, що їх зв’язує. "
    "Таблиці не огороджуй у ``` — інакше вони стануть кодом, а не таблицею. Блоки ``` — лише для справжнього коду. "
    "Без сирого URL у тексті (URL лише в полі «посилання»). Без емодзі, якщо не виняток вище.}\n"
    "текст чату: {короткий нейтральний заголовок теми, як назва розділу. "
    "НЕ питання. Наприклад: «Оцінювання в КПІ», «Стипендії та бал», «Важливо для першокурсників».}\n"
    "емоція: {СТРОГО одне англійське слово: neutral, happy, sad, surprise, thinking, angry, disgust. "
    "neutral — спокійно; happy — радісно; sad — шкода; surprise — здивування; thinking — роздуми; "
    "angry — роздратування; disgust — огида. Лише слово.}\n"
    "посилання: {повний URL з контексту, якщо доречно; інакше рівно: немає}\n"
    "Не додавай нічого за межами цього шаблону. НЕ змінюй назви полів і порядок полів."
)


def _chat_messages(
    user_message: str,
    context: str,
    attachment_block: Optional[str] = None,
) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Контекст:\n{context}"},
    ]
    if attachment_block:
        msgs.append(
            {
                "role": "user",
                "content": (
                    "Нижче — вміст або опис файлу, який надіслав користувач. "
                    "Відповідай на його запит, спираючись на цей вміст разом із контекстом FAQ/сайтів; "
                    "не вигадуй факти про файл, яких там немає.\n\n"
                    + attachment_block
                ),
            }
        )
    msgs.append({"role": "user", "content": user_message})
    return msgs


def _sse_event(obj: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


async def generate_model_answer(
    message: str,
    context: str,
    attachment_block: Optional[str] = None,
) -> str:
    """Викликає модель та повертає сирий текст відповіді."""

    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3",
                messages=_chat_messages(message, context, attachment_block),
            )
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        raise ChatServiceError(f"Помилка при зверненні до LLM: {exc}") from exc

    return response.choices[0].message.content


def _normalize_emotion(raw: str) -> str:
    s = raw.strip().lower().split()[0] if raw.strip() else "neutral"
    if s in ALLOWED_EMOTIONS:
        return s
    emoji_map = {
        "😊": "happy",
        "😄": "happy",
        "😲": "surprise",
        "🤔": "thinking",
        "😍": "happy",
        "😠": "angry",
        "🤢": "disgust",
        "😤": "angry",
    }
    return emoji_map.get(raw.strip(), "neutral")


def _normalize_link(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    if s.lower() in ("немає", "none", "n/a", "-", "null"):
        return ""
    return s


def parse_answer(answer: str) -> Dict[str, str]:
    """Парсить відповідь моделі у структурований вигляд."""

    main_text_match = re.search(
        r"(?i)основний\s*текст\s*:\s*(.+?)(?=\n\s*(текст\s*чату|емоція|посилання)\s*:|\Z)",
        answer,
        re.DOTALL,
    )
    link_match = re.search(r"(?i)посилання\s*:\s*([^\n]+)", answer)
    emotion_match = re.search(r"(?i)емоція\s*:\s*([^\n]+)", answer)
    title_match = re.search(
        r"(?i)текст\s*чату\s*:\s*(.+?)(?=\n\s*(основний\s*текст|емоція|посилання)\s*:|\Z)",
        answer,
        re.DOTALL,
    )

    main_text = (
        main_text_match.group(1).strip()
        if main_text_match
        else "Вибач, не вдалося отримати основний текст відповіді."
    )
    link_raw = link_match.group(1).strip() if link_match else ""
    link = _normalize_link(link_raw)
    emotion_raw = emotion_match.group(1).strip() if emotion_match else "neutral"
    emotion = _normalize_emotion(emotion_raw)
    chat_title = title_match.group(1).strip() if title_match else "Без назви"

    return {
        "answer_raw": answer,
        "response": main_text,
        "link": link,
        "emotion": emotion,
        "title": chat_title,
    }


async def append_user_message(db, session_id: str, user_text: str) -> None:
    """Додає лише повідомлення користувача (для streaming перед генерацією)."""

    user_msg: Dict[str, Any] = {
        "role": "user",
        "text": user_text,
        "timestamp": datetime.utcnow(),
    }
    update_result = await db["sessions"].update_one(
        {"_id": ObjectId(session_id)},
        {"$push": {"messages": user_msg}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    if update_result.modified_count == 0:
        raise ChatSessionNotFound("Сесія не знайдена")


async def append_assistant_message(
    db,
    session_id: str,
    assistant_text: str,
    assistant_emotion: str,
    assistant_link: str,
    assistant_title: str,
) -> None:
    """Додає лише повідомлення асистента."""

    assistant_msg: Dict[str, Any] = {
        "role": "assistant",
        "text": assistant_text,
        "emotion": assistant_emotion,
        "link": assistant_link or "",
        "title": assistant_title,
        "timestamp": datetime.utcnow(),
    }
    update_result = await db["sessions"].update_one(
        {"_id": ObjectId(session_id)},
        {"$push": {"messages": assistant_msg}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    if update_result.modified_count == 0:
        raise ChatSessionNotFound("Сесія не знайдена")

    topic = (assistant_title or "").strip()
    if topic and topic not in _SESSION_NAME_DEFAULTS:
        short = topic[:_SESSION_NAME_MAX_LEN].rstrip()
        if short:
            await db["sessions"].update_one(
                {
                    "_id": ObjectId(session_id),
                    "$or": [
                        {"name": {"$in": list(_SESSION_NAME_DEFAULTS)}},
                        {"name": {"$exists": False}},
                    ],
                },
                {"$set": {"name": short}},
            )


async def append_messages_to_session(
    db,
    session_id: str,
    user_text: str,
    assistant_text: str,
    assistant_emotion: str,
    assistant_link: str,
    assistant_title: str,
) -> None:
    """Оновлює сесію в MongoDB: користувач + асистент (не streaming)."""

    await append_user_message(db, session_id, user_text)
    await append_assistant_message(
        db, session_id, assistant_text, assistant_emotion, assistant_link, assistant_title
    )


def _stream_worker(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    user_message: str,
    context: str,
    attachment_block: Optional[str] = None,
) -> None:
    """Блокуючий збір токенів Together (stream=True) у фоновому потоці."""

    def put(kind: str, payload: Union[str, None] = None) -> None:
        fut = asyncio.run_coroutine_threadsafe(queue.put((kind, payload)), loop)
        fut.result(timeout=120)

    try:
        stream = client.chat.completions.create(
            model="deepseek-ai/DeepSeek-V3",
            messages=_chat_messages(user_message, context, attachment_block),
            stream=True,
        )
        parts: list[str] = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            piece = getattr(delta, "content", None) or ""
            if piece:
                parts.append(piece)
                put("delta", piece)
        put("done", "".join(parts))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        put("error", str(exc))


async def stream_chat_events(
    db,
    session_id: str,
    user_message: str,
    attachment_block: Optional[str] = None,
    attachment_filename: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """
    SSE-потік: status (думає) → delta (фрагменти сирої відповіді) → done (розпарсені поля) або error.
    Користувача записує в БД одразу; асистента — після повної відповіді.
    """

    cleaned = (user_message or "").strip() or "Проаналізуй вкладений файл."
    display_user = cleaned
    if attachment_filename:
        display_user = f"{cleaned}\n\n📎 {attachment_filename}"

    # ── Аналіз емоції КОРИСТУВАЧА через EmotionEngine ──────────────────────
    # Це незалежний NLP-аналіз тексту до запиту до LLM.
    # Результат відправляється фронтенду окремо від емоції асистента.
    user_emotion_result = analyze_emotion(cleaned)
    user_emotion_data = {
        "emotion":     user_emotion_result.emotion,
        "confidence":  round(user_emotion_result.confidence, 4),
        "scores":      {k: round(v, 4) for k, v in user_emotion_result.scores.items()},
        "method":      user_emotion_result.method,
        "tokens":      user_emotion_result.tokens_matched[:10],  # топ-10 для дебагу
        "avatar_filename": get_avatar_controller().select_animation(
            user_emotion_result, demo_responsive=True
        ).filename,
    }

    await append_user_message(db, session_id, display_user)
    yield _sse_event({"type": "status", "phase": "thinking"})
    # Одразу повідомляємо фронтенд про емоцію користувача (до відповіді LLM)
    yield _sse_event({"type": "user_emotion", **user_emotion_data})

    context = await build_rag_context(db, cleaned)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    thread = threading.Thread(
        target=_stream_worker,
        args=(loop, queue, cleaned, context, attachment_block),
        daemon=True,
    )
    thread.start()

    while True:
        kind, payload = await queue.get()
        if kind == "delta" and payload:
            yield _sse_event({"type": "delta", "content": payload})
        elif kind == "done":
            raw = payload or ""
            parsed = parse_answer(raw)
            await append_assistant_message(
                db,
                session_id,
                parsed["response"],
                parsed["emotion"],
                parsed["link"],
                parsed["title"],
            )
            # Аналізуємо емоцію ВІДПОВІДІ асистента через той самий EmotionEngine
            assistant_emotion_result = analyze_emotion(parsed["response"])
            assistant_emotion_data = {
                "emotion":    assistant_emotion_result.emotion,
                "confidence": round(assistant_emotion_result.confidence, 4),
                "scores":     {k: round(v, 4) for k, v in assistant_emotion_result.scores.items()},
                "method":     assistant_emotion_result.method,
                "avatar_filename": get_avatar_controller().select_animation(
                    assistant_emotion_result, demo_responsive=True
                ).filename,
            }
            yield _sse_event(
                {
                    "type": "done",
                    "response": parsed["response"],
                    "link": parsed["link"],
                    "emotion": parsed["emotion"],
                    "title": parsed["title"],
                    "user_emotion": user_emotion_data,
                    "assistant_emotion": assistant_emotion_data,
                }
            )
            return
        elif kind == "error":
            yield _sse_event(
                {"type": "error", "detail": payload or "Помилка генерації"}
            )
            return


async def process_chat(
    db,
    message: str,
    session_id: str,
    attachment_block: Optional[str] = None,
    attachment_filename: Optional[str] = None,
) -> Dict[str, str]:
    """
    Головна точка входу сервісу чату:
    - збирає контекст,
    - викликає модель,
    - парсить відповідь,
    - оновлює сесію в базі.
    """

    cleaned_message = (message or "").strip() or "Проаналізуй вкладений файл."
    display_user = cleaned_message
    if attachment_filename:
        display_user = f"{cleaned_message}\n\n📎 {attachment_filename}"

    # NLP-аналіз емоції користувача (не-streaming шлях)
    user_emotion_result = analyze_emotion(cleaned_message)

    context = await build_rag_context(db, cleaned_message)
    answer = await generate_model_answer(
        cleaned_message, context, attachment_block
    )
    parsed = parse_answer(answer)

    await append_messages_to_session(
        db=db,
        session_id=session_id,
        user_text=display_user,
        assistant_text=parsed["response"],
        assistant_emotion=parsed["emotion"],
        assistant_link=parsed["link"],
        assistant_title=parsed["title"],
    )

    parsed["user_emotion"] = {
        "emotion":    user_emotion_result.emotion,
        "confidence": round(user_emotion_result.confidence, 4),
        "scores":     {k: round(v, 4) for k, v in user_emotion_result.scores.items()},
        "method":     user_emotion_result.method,
        "avatar_filename": get_avatar_controller().select_animation(
            user_emotion_result, demo_responsive=True
        ).filename,
    }

    # Аналізуємо емоцію ВІДПОВІДІ асистента через EmotionEngine
    assistant_emotion_result = analyze_emotion(parsed["response"])
    parsed["assistant_emotion"] = {
        "emotion":    assistant_emotion_result.emotion,
        "confidence": round(assistant_emotion_result.confidence, 4),
        "scores":     {k: round(v, 4) for k, v in assistant_emotion_result.scores.items()},
        "method":     assistant_emotion_result.method,
        "avatar_filename": get_avatar_controller().select_animation(
            assistant_emotion_result, demo_responsive=True
        ).filename,
    }

    return parsed


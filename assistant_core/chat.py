import asyncio
import os
import re
import traceback
from datetime import datetime
from typing import Any, Dict, FrozenSet

from bson import ObjectId
from dotenv import load_dotenv
from together import Together


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
    {"neutral", "happy", "sad", "surprise", "thinking"}
)


async def build_links_context(db) -> str:
    """Збирає контекст із колекції links."""
    links = await db["links"].find().to_list(None)
    return "\n\n".join(
        f"{i + 1}) Назва: {link['title']}\n"
        f"Опис: {link['description']}\n"
        f"Теги: {', '.join(link['tags'])}\n"
        f"Посилання: {link['url']}"
        for i, link in enumerate(links)
        if link.get("visible")
    )


async def generate_model_answer(message: str, context: str) -> str:
    """Викликає модель та повертає сирий текст відповіді."""

    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ти — чат-асистент для студентів КПІ, який завжди щасливий і любить багато розмовляти. "
                            "Відповідай активно і доброзичливо, ніби ти веселий і товариський співрозмовник. "
                            "Використовуй емоції в тексті — іноді проявляй здивування, іноді задумуйся (наприклад, додавай хм…, ого!, вау!). "
                            "Якщо користувач ставить офіційне запитання щодо навчання чи КПІ, дай чітку, інформативну відповідь. "
                            "❗ Для таких відповідей використовуй лише ту інформацію, яка є в контексті (отримана з бази даних). "
                            "Посилання ти можеш використовувати тільки з контексту. "
                            "Не вигадуй нові посилання."
                            "Якщо користувач просто хоче поспілкуватися, підтримай розмову дружньо і весело, обов’язково вказуй, що це — твоя особиста думка. "
                            "У таких випадках можеш бути емоційним, живим, додавати емодзі, емоційні вигуки. "
                            "⚠️ ВАЖЛИВО: Ти ЗАВЖДИ надаєш відповідь ТІЛЬКИ в одному строго визначеному форматі. "
                            "Не додаєш жодного слова, жодного речення, жодного пояснення за межами шаблону. Не ігноруєш структуру. Формат відповіді:\n"
                            "текст чату: {У полі \"текст чату\" завжди генеруй короткий, емоційний заголовок, ніби це назва розділу чату. "
                            "НЕ пиши питання і не формулюй як відповідь. Заголовок має бути коротким, яскравим і привертати увагу, "
                            "наприклад: \"Оцінювання в КПІ\", \"Стипендії та бал\", \"Реєстрація на курси\", \"Важливо для першокурсників\", "
                            "\"Вау! Нові можливості ✨\" тощо.}\n"
                            "основний текст: {розгорнута відповідь у Markdown: **жирний**, списки, абзаци; без сирого URL (URL лише в полі «посилання»). "
                            "Емодзі можна.}\n"
                            "емоція: {СТРОГО одне англійське слово з цього списку: neutral, happy, sad, surprise, thinking. "
                            "neutral — спокійно; happy — радісно/підтримка; sad — шкода/сум; surprise — здивування; thinking — роздуми. "
                            "Без лапок, без пояснень, лише слово.}\n"
                            "посилання: {повний URL з контексту, якщо доречно; інакше рівно слово: немає}\n"
                            "Не додавай нічого за межами цього шаблону. НЕ починай речення без поля. НЕ додавай пояснень. НЕ змінюй структуру."
                        ),
                    },
                    {"role": "user", "content": f"Контекст:\n{context}"},
                    {"role": "user", "content": message},
                ],
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
        r"(?i)основний\s*текст\s*:\s*(.+?)(?:\n\s*(емоція|посилання)\s*:)",
        answer,
        re.DOTALL,
    )
    link_match = re.search(r"(?i)посилання\s*:\s*([^\n]+)", answer)
    emotion_match = re.search(r"(?i)емоція\s*:\s*([^\n]+)", answer)
    title_match = re.search(
        r"(?i)текст\s*чату\s*:\s*(.+?)(?:\n\s*(основний\s*текст|емоція|посилання)\s*:)",
        answer,
        re.DOTALL,
    )

    main_text = (
        main_text_match.group(1).strip()
        if main_text_match
        else "Вибач, не вдалося отримати основний текст 😢"
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


async def append_messages_to_session(
    db,
    session_id: str,
    user_text: str,
    assistant_text: str,
    assistant_emotion: str,
    assistant_link: str,
    assistant_title: str,
) -> None:
    """Оновлює сесію в MongoDB, додаючи повідомлення користувача та асистента."""

    user_msg: Dict[str, Any] = {
        "role": "user",
        "text": user_text,
        "timestamp": datetime.utcnow(),
    }
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
        {
            "$push": {"messages": {"$each": [user_msg, assistant_msg]}},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )

    if update_result.modified_count == 0:
        raise ChatSessionNotFound("Сесія не знайдена")


async def process_chat(db, message: str, session_id: str) -> Dict[str, str]:
    """
    Головна точка входу сервісу чату:
    - збирає контекст,
    - викликає модель,
    - парсить відповідь,
    - оновлює сесію в базі.
    """

    cleaned_message = message.strip()
    context = await build_links_context(db)
    answer = await generate_model_answer(cleaned_message, context)
    parsed = parse_answer(answer)

    await append_messages_to_session(
        db=db,
        session_id=session_id,
        user_text=cleaned_message,
        assistant_text=parsed["response"],
        assistant_emotion=parsed["emotion"],
        assistant_link=parsed["link"],
        assistant_title=parsed["title"],
    )

    return parsed


"""
assistant_core/emotion_engine.py
══════════════════════════════════════════════════════════════════════════════
Модуль аналізу емоційного забарвлення тексту та керування реакціями 3D-аватара.

АРХІТЕКТУРА СИСТЕМИ
═══════════════════

    Текст користувача
          │
          ▼
    ┌──────────────────────────────────────────────────────────┐
    │  EmotionClassifier.analyze(text)                         │
    │                                                          │
    │  1. Препроцесинг (normalize, tokenize)                   │
    │  2. LexiconScorer     → lexicon_scores                   │
    │  3. PatternAnalyzer   → pattern_scores                   │
    │  4. EmotionMLClassifier → ml_scores  (TF-IDF + LogReg)   │
    │  5. ScoreAggregator   → weighted combine                 │
    │  6. IntensityModifier → amplified_scores                 │
    │  7. NegationProcessor → adjusted_scores                  │
    │  8. ScoreAggregator.softmax → probability distribution   │
    │  9. ContextWindow     → temporal smoothing               │
    │ 10. TransitionMatrix  → allowed transition               │
    └──────────────────────────────────────────────────────────┘
          │
          ▼
    AvatarController.select_animation(emotion, confidence)
          │
          ▼
    3D Avatar (MP4 відео-анімація)

КОМПОНЕНТИ
══════════
  EmotionLexicon       – словник слів → {emotion: score}  (українська мова)
  PatternAnalyzer      – regex-шаблони для синтаксичних / пунктуаційних маркерів
  EmotionMLClassifier  – ML-модель (sklearn TF-IDF + Logistic Regression)
  IntensityModifier    – модифікатори інтенсивності ("дуже", "надзвичайно", ...)
  NegationProcessor    – обробка заперечень ("не", "зовсім не", "ніяк", ...)
  ScoreAggregator      – об'єднання всіх сигналів → вектор ймовірностей (softmax)
  ContextWindow        – пам'ять останніх K станів (часове згладжування)
  TransitionMatrix     – матриця допустимих переходів між емоціями
  EmotionClassifier    – головний класифікатор (оркестратор всіх компонентів)
  AvatarController     – вибір анімаційного файлу за емоцією + впевненістю
"""

from __future__ import annotations

import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, FrozenSet, List, Optional, Tuple

from assistant_core.ml_classifier import (
    EmotionMLClassifier,
    get_ml_classifier,
    is_ml_available,
)

# ══════════════════════════════════════════════════════════════════════════════
# КОНСТАНТИ ТА ТИПИ
# ══════════════════════════════════════════════════════════════════════════════

Emotion = str  # 7 класів — у відповідності до кліпів у avatar/animations/

EMOTIONS: FrozenSet[Emotion] = frozenset(
    {"neutral", "happy", "sad", "surprise", "thinking", "angry", "disgust"}
)
EMOTION_LIST: List[Emotion] = [
    "neutral",
    "happy",
    "sad",
    "surprise",
    "thinking",
    "angry",
    "disgust",
]

# Фактичний набір .mp4 у avatar/animations/ — усі використовуються в ANIMATION_MAP (high/med/fallback).
ANIMATION_CLIP_FILES: Tuple[str, ...] = (
    "angry.mp4",
    "confused.mp4",
    "disgust.mp4",
    "excited.mp4",
    "fear.mp4",
    "happy.mp4",
    "muse.mp4",
    "sad.mp4",
    "speak_blink.mp4",
    "speak.mp4",
    "squinted1.mp4",
    "surprize1.mp4",
)

# Мінімальна впевненість для зміни стану аватара (нижче — лишаємо neutral)
CONFIDENCE_THRESHOLD = 0.30

# На переході neutral → не-neutral: окремий нижній поріг product(confidence, score),
# бо після softmax на 7 класах argmax часто ~0.14–0.18 (агрегатор уже відсіяв «ніякого сигналу»).
NEUTRAL_EXIT_CONFIDENCE_FACTOR = 0.12

# Якщо softmax-лідер ≠ поточний стан аватара: дозволити перехід до лідера при слабшому product,
# інакше «застряє» self-loop (happy→happy з p≈0.12), хоча діаграма показує thinking/sad тощо.
SOFTMAX_TOP_TRANSITION_FACTOR = 0.15

# Розмір вікна контексту (кількість попередніх результатів, що впливають на поточний)
CONTEXT_WINDOW_SIZE = 5

# Вага контексту у фінальному рішенні (0.0 = без згладжування, 1.0 = тільки контекст)
CONTEXT_SMOOTHING_ALPHA = 0.25

# Мінімальна кількість балів для виходу з нейтральної зони
NEUTRAL_FLOOR_SCORE = 1.5


# ══════════════════════════════════════════════════════════════════════════════
# РЕЗУЛЬТАТИ АНАЛІЗУ
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EmotionResult:
    """
    Результат класифікації емоції.

    Поля:
        emotion          – найімовірніша емоція
        confidence       – впевненість моделі [0.0 … 1.0]
        scores           – розподіл по всіх класах (сума = 1.0 після softmax)
        raw_scores       – сирі бали до нормалізації
        method           – метод визначення ("lexicon" | "pattern" | "ml" | "hybrid")
        context_smoothed – чи застосовувалось контекстне згладжування
        tokens_matched   – список токенів зі словника, що вплинули на рішення
        component_scores – внесок кожного компонента (для пояснення / диплому):
                           {"lexicon": {...}, "pattern": {...}, "ml": {...}}
        ml_used          – чи активна ML-модель у цьому запиті
    """
    emotion: Emotion
    confidence: float
    scores: Dict[Emotion, float]
    raw_scores: Dict[Emotion, float]
    method: str
    context_smoothed: bool = False
    tokens_matched: List[str] = field(default_factory=list)
    component_scores: Dict[str, Dict[Emotion, float]] = field(default_factory=dict)
    ml_used: bool = False

    def to_dict(self) -> Dict:
        return {
            "emotion": self.emotion,
            "confidence": round(self.confidence, 4),
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "raw_scores": {k: round(v, 4) for k, v in self.raw_scores.items()},
            "method": self.method,
            "context_smoothed": self.context_smoothed,
            "tokens_matched": self.tokens_matched,
            "component_scores": {
                comp: {k: round(v, 4) for k, v in vec.items()}
                for comp, vec in self.component_scores.items()
            },
            "ml_used": self.ml_used,
        }


@dataclass
class AvatarAnimation:
    """Анімація аватара."""
    filename: str        # назва відео-файлу (у папці avatar/animations/)
    emotion: Emotion
    priority: int        # вищий = важливіший (0 – найнижчий)
    loop: bool = True    # чи програвати у петлі


# ══════════════════════════════════════════════════════════════════════════════
# 1. ЛЕКСИКОН ЕМОЦІЙ
# ══════════════════════════════════════════════════════════════════════════════

class EmotionLexicon:
    """
    Лексикон емоційно-забарвлених слів (українська мова + emoji).

    Структура запису: слово → {емоція: вага}
    Вага: 1.0 – слабкий сигнал, 5.0 – дуже сильний сигнал.

    Алгоритм лексичного оцінювання:
    ────────────────────────────────
    1. Кожен токен нормалізується (lowercase, видалення пунктуації)
    2. Пошук у словнику — O(1) завдяки хеш-таблиці
    3. Знайдені ваги підсумовуються по категоріях емоцій
    4. Повертається вектор сирих балів {emotion: total_score}
    """

    LEXICON: Dict[str, Dict[Emotion, float]] = {
        # ── HAPPY ─────────────────────────────────────────────────────────────
        "дякую":       {"happy": 2.5},
        "дяку":        {"happy": 1.5},
        "вдячний":     {"happy": 3.0},
        "вдячна":      {"happy": 3.0},
        "вдячність":   {"happy": 3.0},
        "радий":       {"happy": 3.5},
        "рада":        {"happy": 3.5},
        "радість":     {"happy": 3.5},
        "радіти":      {"happy": 3.0},
        "радіє":       {"happy": 3.0},
        "щасливий":    {"happy": 4.0},
        "щаслива":     {"happy": 4.0},
        "щастя":       {"happy": 4.0},
        "щасливо":     {"happy": 3.5},
        "чудово":      {"happy": 3.5},
        "чудовий":     {"happy": 3.0},
        "чудова":      {"happy": 3.0},
        "відмінно":    {"happy": 3.5},
        "відмінний":   {"happy": 3.0},
        "відмінна":    {"happy": 3.0},
        "прекрасно":   {"happy": 3.5},
        "прекрасний":  {"happy": 3.0},
        "прекрасна":   {"happy": 3.0},
        "крутий":      {"happy": 3.0},
        "круто":       {"happy": 3.0},
        "класно":      {"happy": 3.0},
        "класний":     {"happy": 3.0},
        "класна":      {"happy": 3.0},
        "круть":       {"happy": 3.5},
        "супер":       {"happy": 3.5},
        "дивовижно":   {"happy": 3.5},
        "дивовижний":  {"happy": 3.0},
        "добре":       {"happy": 2.5},
        "добрий":      {"happy": 2.0},
        "добра":       {"happy": 2.0},
        "хороший":     {"happy": 2.5},
        "хороша":      {"happy": 2.5},
        "хороше":      {"happy": 2.5},
        "хороші":      {"happy": 2.5},
        "хорошими":    {"happy": 2.5},
        "хорошою":     {"happy": 3.0},
        "хорошому":    {"happy": 2.5},
        "хороших":     {"happy": 2.5},
        "оцінка":      {"happy": 2.5},
        "оцінки":      {"happy": 2.0},
        "оцінкою":     {"happy": 2.5},
        "оцінку":      {"happy": 2.0},
        "гарно":       {"happy": 2.5},
        "гарний":      {"happy": 2.0},
        "гарна":       {"happy": 2.0},
        "красиво":     {"happy": 2.5},
        "красивий":    {"happy": 2.0},
        "красива":     {"happy": 2.0},
        "люблю":       {"happy": 3.5},
        "любов":       {"happy": 3.5},
        "кохаю":       {"happy": 4.0},
        "кохання":     {"happy": 4.0},
        "обожнюю":     {"happy": 3.5},
        "подобається": {"happy": 2.5},
        "сподобалось": {"happy": 3.0},
        "сподобалося": {"happy": 3.0},
        "вау":         {"happy": 3.0, "surprise": 2.0},
        "wow":         {"happy": 2.5, "surprise": 2.5},
        "ура":         {"happy": 4.0},
        "хаха":        {"happy": 3.0},
        "хахаха":      {"happy": 3.5},
        "хахахаха":    {"happy": 4.0},
        "лол":         {"happy": 2.5},
        "смішно":      {"happy": 3.0},
        "смішний":     {"happy": 2.5},
        "задоволений": {"happy": 3.0},
        "задоволена":  {"happy": 3.0},
        "задоволення": {"happy": 3.0},
        "позитив":     {"happy": 3.0},
        "позитивний":  {"happy": 2.5},
        "позитивно":   {"happy": 2.5},
        "успіх":       {"happy": 3.0},
        "успішно":     {"happy": 3.0},
        "перемога":    {"happy": 4.0},
        "виграв":      {"happy": 3.5},
        "виграла":     {"happy": 3.5},
        "здорово":     {"happy": 3.0},
        "найкращий":   {"happy": 3.5},
        "найкраща":    {"happy": 3.5},
        "бомба":       {"happy": 2.5},
        "вогонь":      {"happy": 2.5},
        "топ":         {"happy": 2.5},
        "молодець":    {"happy": 3.0},
        "молодчина":   {"happy": 3.5},
        "браво":       {"happy": 3.5},
        "кайф":        {"happy": 3.5},
        "кайфово":     {"happy": 3.5},
        "афігєно":     {"happy": 3.0},
        "афігенно":    {"happy": 3.0},
        "обалденно":   {"happy": 3.0},
        "незабутньо":  {"happy": 3.5},
        "пишаюся":     {"happy": 3.5},
        "пишається":   {"happy": 3.2},
        "гордий":      {"happy": 2.8},
        "горда":       {"happy": 2.8},
        "розчулений":  {"happy": 3.0},
        "розчулена":   {"happy": 3.0},
        "надзвичайно": {"happy": 3.5, "surprise": 2.0},
        "збулося":     {"happy": 3.5},
        "ідеально":    {"happy": 3.5},
        "бездоганно":  {"happy": 3.5},
        "нагорода":    {"happy": 3.2},
        "запрошення":  {"happy": 2.8},
        "зарахували":  {"happy": 3.2},
        "зараховано":  {"happy": 3.5},
        "склав":       {"happy": 2.2},  # у позитивних конструкціях; конфлікти змінюють патерн/ML
        "здала":       {"happy": 2.2},
        "здали":       {"happy": 2.2},
        # emoji happy
        "😊": {"happy": 3.5},
        "😃": {"happy": 4.0},
        "😄": {"happy": 4.0},
        "😁": {"happy": 3.5},
        "😍": {"happy": 4.0},
        "🥰": {"happy": 4.0},
        "😂": {"happy": 3.5},
        "🤣": {"happy": 4.0},
        "👍": {"happy": 2.5},
        "💯": {"happy": 3.5},
        "🎉": {"happy": 3.5},
        "🎊": {"happy": 3.5},
        "❤️": {"happy": 3.5},
        "🔥": {"happy": 2.5, "surprise": 1.5},
        "✅": {"happy": 2.5},
        "🌟": {"happy": 2.5},

        # ── SAD ───────────────────────────────────────────────────────────────
        "сумно":       {"sad": 3.5},
        "сумний":      {"sad": 3.0},
        "сумна":       {"sad": 3.0},
        "сум":         {"sad": 3.0},
        "смуток":      {"sad": 3.5},
        "сумую":       {"sad": 3.5},
        "сумував":     {"sad": 3.0},
        "сумувала":    {"sad": 3.0},
        "плачу":       {"sad": 4.0},
        "плакати":     {"sad": 3.5},
        "плаче":       {"sad": 3.5},
        "сльози":      {"sad": 3.5},
        "слізки":      {"sad": 3.5},
        "шкода":       {"sad": 3.0},
        "жаль":        {"sad": 2.5},
        "жаліти":      {"sad": 2.5},
        "жалість":     {"sad": 2.5},
        "нажаль":      {"sad": 3.0},
        "тяжко":       {"sad": 3.0},
        "тяжкий":      {"sad": 2.5},
        "тяжка":       {"sad": 2.5},
        "важко":       {"sad": 2.5},
        "погано":      {"sad": 3.0, "disgust": 0.8},
        "поганий":     {"sad": 2.5, "angry": 0.8},
        "погана":      {"sad": 2.5, "angry": 0.8},
        "жахливо":     {"sad": 4.0, "disgust": 1.2},
        "жахливий":    {"sad": 3.5},
        "жахлива":     {"sad": 3.5},
        "жах":         {"sad": 3.0},
        "страшно":     {"sad": 3.0},
        "страх":       {"sad": 2.5},
        "боюсь":       {"sad": 2.5},
        "боюся":       {"sad": 2.5},
        "біда":        {"sad": 3.5},
        "горе":        {"sad": 4.0},
        "проблема":    {"sad": 2.0, "thinking": 1.0},
        "помилка":     {"sad": 1.5, "thinking": 1.5},
        "невдача":     {"sad": 3.5},
        "незарах":    {"sad": 3.5},
        "незарахування": {"sad": 3.2},
        "залік":       {"sad": 2.0, "thinking": 2.5},
        "заліки":      {"sad": 1.8, "thinking": 2.0},
        "заліку":      {"sad": 2.0},
        "перездача":   {"sad": 3.0, "thinking": 1.2},
        "провалився":  {"sad": 3.5},
        "провалилася": {"sad": 3.5},
        "провалив":    {"sad": 3.5},
        "провалила":   {"sad": 3.5},
        "втомився":    {"sad": 3.0},
        "втомилась":   {"sad": 3.0},
        "втомилася":   {"sad": 3.0},
        "втома":       {"sad": 3.0},
        "стрес":       {"sad": 3.0},
        "тривога":     {"sad": 2.5},
        "тривожно":    {"sad": 2.5},
        "нудно":       {"sad": 2.0},
        "скучно":      {"sad": 2.0},
        "самотньо":    {"sad": 3.5},
        "самотній":    {"sad": 3.0},
        "самотня":     {"sad": 3.0},
        "самотність":  {"sad": 3.5},
        "розчарований": {"sad": 3.5, "thinking": 1.0},
        "розчарована": {"sad": 3.5, "thinking": 1.0},
        "засмучений":  {"sad": 3.5},
        "засмучена":   {"sad": 3.5},
        "прикро":      {"sad": 3.5},
        "образився":   {"sad": 3.0},
        "образилася":   {"sad": 3.0},
        "сенсу":       {"sad": 3.5, "thinking": 3.5},
        "безглуздо":   {"sad": 3.0, "thinking": 2.5},
        "депресія":    {"sad": 4.5},
        "депресивно":   {"sad": 4.0},
        "депресивний": {"sad": 4.0},
        "безнадія":    {"sad": 4.5},
        "безнадійно": {"sad": 4.5},
        "апатія":      {"sad": 4.0, "thinking": 1.5},
        "апатично":    {"sad": 3.5},
        "вигорів":     {"sad": 3.5},
        "вигоріла":    {"sad": 3.5},
        "порожньо":    {"sad": 3.5},
        "розгублений": {"sad": 3.2, "thinking": 2.5},
        "розгублена":  {"sad": 3.2, "thinking": 2.5},
        "засмутило":   {"sad": 3.5},
        "розбитий":    {"sad": 4.0},
        "розбита":    {"sad": 4.0},
        "втратив":      {"sad": 3.5},
        "втратила":     {"sad": 3.5},
        "втрачаю":      {"sad": 4.0},
        # emoji sad
        "😢": {"sad": 4.0},
        "😭": {"sad": 4.5},
        "😞": {"sad": 3.5},
        "😔": {"sad": 3.0},
        "💔": {"sad": 4.0},
        "😿": {"sad": 3.5},
        "☹️": {"sad": 3.0},
        "😓": {"sad": 3.0},
        "😩": {"sad": 3.5},
        "😫": {"sad": 3.5},
        "😰": {"sad": 3.0},
        "😟": {"sad": 3.0},

        # ── SURPRISE ──────────────────────────────────────────────────────────
        "ого":          {"surprise": 4.0},
        "ой":           {"surprise": 3.0},
        "ах":           {"surprise": 3.0},
        "оо":           {"surprise": 3.0},
        "ооо":          {"surprise": 3.5},
        "оооо":         {"surprise": 4.0},
        "неймовірно":   {"surprise": 4.0},
        "неймовірний":  {"surprise": 3.5},
        "неймовірна":   {"surprise": 3.5},
        "неможливо":    {"surprise": 3.5},
        "несподівано":  {"surprise": 4.0},
        "несподіваний": {"surprise": 3.5},
        "несподівана":  {"surprise": 3.5},
        "несподіванка": {"surprise": 4.0},
        "раптово":      {"surprise": 3.5},
        "раптовий":     {"surprise": 3.0},
        "раптова":      {"surprise": 3.0},
        "шок":          {"surprise": 4.0},
        "шокований":    {"surprise": 4.0},
        "шокована":     {"surprise": 4.0},
        "здивований":   {"surprise": 3.5},
        "здивована":    {"surprise": 3.5},
        "здивування":   {"surprise": 3.5},
        "захоплення":   {"surprise": 2.5, "happy": 2.0},
        "вражений":     {"surprise": 3.0, "happy": 1.5},
        "вражена":      {"surprise": 3.0, "happy": 1.5},
        "серйозно":     {"surprise": 2.5, "thinking": 1.5},
        "справді":      {"surprise": 2.0},
        "реально":      {"surprise": 2.5},
        "дивно":        {"surprise": 3.5, "thinking": 2.0},
        "дивина":       {"surprise": 3.5},
        "незвично":     {"surprise": 3.0},
        "незвичайний":  {"surprise": 3.0},
        # emoji surprise
        "😲": {"surprise": 4.5},
        "😱": {"surprise": 4.5},
        "🤯": {"surprise": 5.0},
        "😮": {"surprise": 4.0},
        "🙀": {"surprise": 4.0},
        "😳": {"surprise": 3.5},
        "👀": {"surprise": 2.5},

        # ── THINKING ──────────────────────────────────────────────────────────
        "цікаво":       {"thinking": 3.0},
        "цікавий":      {"thinking": 2.5},
        "цікава":       {"thinking": 2.5},
        "цікавлюсь":    {"thinking": 3.0},
        "цікавить":     {"thinking": 3.0},
        "думаю":        {"thinking": 2.5},
        "думав":        {"thinking": 2.0},
        "думала":       {"thinking": 2.0},
        "роздумую":     {"thinking": 3.0},
        "міркую":       {"thinking": 3.0},
        "розмірковую":  {"thinking": 3.0},
        "аналізую":     {"thinking": 3.0},
        "аналіз":       {"thinking": 2.5},
        "мабуть":       {"thinking": 2.0},
        "напевно":      {"thinking": 1.5},
        "можливо":      {"thinking": 2.0},
        "певно":        {"thinking": 1.5},
        "здається":     {"thinking": 1.5},
        "схоже":        {"thinking": 1.5},
        "якщо":         {"thinking": 1.0},
        "задумливий":   {"thinking": 3.0},
        "задумлива":    {"thinking": 3.0},
        "складно":      {"thinking": 2.0, "sad": 1.0},
        "складний":     {"thinking": 1.5},
        "складна":      {"thinking": 1.5},
        "передбачаю":   {"thinking": 2.0},
        "прогнозую":    {"thinking": 2.0},
        "поясни":       {"thinking": 3.2},
        "пояснити":     {"thinking": 3.0},
        "розкажи":      {"thinking": 2.8},
        "розповісти":   {"thinking": 2.5},
        "логіка":       {"thinking": 2.5},
        "перевіряю":    {"thinking": 2.5},
        "звучить":       {"thinking": 2.0},
        "сумніваюсь":   {"thinking": 3.5},
        "сумніваюся":    {"thinking": 3.5},
        "сумніви":       {"thinking": 2.8},
        "незрозуміло":  {"thinking": 2.5},
        "незрозумілий": {"thinking": 2.0},
        "незрозуміла":  {"thinking": 2.0},
        # emoji thinking
        "🤔": {"thinking": 4.5},
        "🧐": {"thinking": 4.0},
        "💭": {"thinking": 3.5},
        "❓": {"thinking": 3.5},
        "🤷": {"thinking": 2.5},
        "🤷‍♂️": {"thinking": 2.5},
        "🤷‍♀️": {"thinking": 2.5},
        "🧩": {"thinking": 2.5},

        # ── ANGRY ─────────────────────────────────────────────────────────────
        "злий":        {"angry": 3.5},
        "зла":         {"angry": 3.5},
        "зле":         {"angry": 3.5},
        "злість":      {"angry": 3.5},
        "злюсь":       {"angry": 4.0},
        "злюся":       {"angry": 4.0},
        "бісить":      {"angry": 4.0},
        "бісить!":     {"angry": 4.0},
        "задрало":     {"angry": 3.5},
        "дратує":      {"angry": 3.0},
        "дістало":     {"angry": 4.2},
        "ненавиджу":   {"angry": 4.5},
        "ненависний":  {"angry": 3.5},
        "ненависна":   {"angry": 3.5},
        "розлючений":  {"angry": 4.0},
        "розлючена":   {"angry": 4.0},
        "розізлився":  {"angry": 4.0},
        "розізлилась": {"angry": 4.0},
        "розізлилася": {"angry": 4.0},
        "лютий":       {"angry": 3.5},
        "люта":        {"angry": 3.5},
        "ярістний":    {"angry": 3.5},
        "ярісна":      {"angry": 3.5},
        "ярість":      {"angry": 3.5},
        "дурниця":     {"angry": 2.5, "disgust": 1.0},
        "чорт":        {"angry": 3.0},
        "блін":        {"angry": 2.0},
        "вбіса":       {"angry": 3.5},
        "бісовий":     {"angry": 3.0},
        "чудовищно":   {"angry": 2.5},
        "😠": {"angry": 4.5},
        "😡": {"angry": 5.0},
        "🤬": {"angry": 5.0},
        "💢": {"angry": 3.5},

        # ── DISGUST ───────────────────────────────────────────────────────────
        "огидно":      {"disgust": 4.0},
        "огидний":     {"disgust": 3.5},
        "огидна":      {"disgust": 3.5},
        "мерзенно":    {"disgust": 3.5},
        "мерзенний":   {"disgust": 3.5},
        "мерзенна":    {"disgust": 3.5},
        "гидко":       {"disgust": 3.5},
        "гидота":      {"disgust": 3.5},
        "тошно":       {"disgust": 3.5},
        "бридко":      {"disgust": 3.5},
        "бридота":     {"disgust": 3.5},
        "бридкий":     {"disgust": 3.5},
        "бридка":      {"disgust": 3.5},
        "фу":          {"disgust": 3.5},
        "фі":          {"disgust": 3.0},
        "відраза":     {"disgust": 4.0},
        "мерзота":     {"disgust": 3.8},
        "смердить":    {"disgust": 3.5},
        "🤢": {"disgust": 4.5},
        "🤮": {"disgust": 4.5},
        "🤧": {"disgust": 2.0},

        # ── NEUTRAL / фактичний тон ──────────────────────────────────────────
        # Вітання та прощання — переважно нейтральні (службові), щоб один «привіт» не ставив радість вище теми розмови
        "привіт":     {"neutral": 3.5, "happy": 0.5},
        "привітик":    {"neutral": 3.2, "happy": 0.4},
        "привітулі":    {"neutral": 3.2, "happy": 0.4},
        "здрастуй":    {"neutral": 3.2},
        "здрастуйте":  {"neutral": 3.5},
        "добридень":    {"neutral": 3.2},
        "добраніч":    {"neutral": 3.0},
        "бувай":       {"neutral": 2.8},
        "побачимось":  {"neutral": 2.8},
        "вітаю":       {"neutral": 3.0, "happy": 1.0},
        "вітання":     {"neutral": 3.0, "happy": 0.8},
        "вітай":       {"neutral": 2.8},
        "нейтрально": {"neutral": 3.0},
        "звичайно":    {"neutral": 2.8},
        "нормально":   {"neutral": 2.5, "happy": 1.0},
        "так-так":    {"neutral": 2.0},
        "ок":         {"neutral": 2.5, "happy": 1.0},
        "окей":       {"neutral": 2.5, "happy": 1.0},
        "зрозуміло":  {"neutral": 3.0},
        "байдуже":    {"neutral": 3.0},
        "пофіг":      {"neutral": 3.0},
        "може":       {"neutral": 2.5, "thinking": 1.5},
        "подивимось": {"neutral": 2.5, "thinking": 2.0},
        "подивімось": {"neutral": 2.5, "thinking": 2.0},
        "факт":       {"neutral": 2.5, "thinking": 1.5},
        "інформація": {"neutral": 2.5},
        "розклад":    {"neutral": 2.8, "thinking": 1.8},
        "документ":   {"neutral": 2.5},
        "посилання":  {"neutral": 2.5},
        "поки":       {"neutral": 1.8},
        "потім":      {"neutral": 2.0},
    }

    def __init__(self) -> None:
        # Нормалізований словник для O(1) пошуку
        self._lookup: Dict[str, Dict[Emotion, float]] = {
            k.lower(): v for k, v in self.LEXICON.items()
        }

    def score(self, tokens: List[str]) -> Tuple[Dict[Emotion, float], List[str]]:
        """
        Лексичне оцінювання тексту.

        Параметри:
            tokens – список нормалізованих токенів

        Повертає:
            (scores, matched_tokens) — вектор балів і список спрацьованих слів
        """
        scores: Dict[Emotion, float] = {e: 0.0 for e in EMOTION_LIST}
        matched: List[str] = []

        for tok in tokens:
            # Пошук точного збігу
            if tok in self._lookup:
                for emotion, weight in self._lookup[tok].items():
                    scores[emotion] += weight
                matched.append(tok)
                continue

            # Пошук стему (перші 5+ символів) для флективних форм
            if len(tok) >= 5:
                stem = tok[:5]
                for key in self._lookup:
                    if key.startswith(stem) and abs(len(key) - len(tok)) <= 3:
                        for emotion, weight in self._lookup[key].items():
                            # Стем дає 70% від повної ваги
                            scores[emotion] += weight * 0.7
                        matched.append(f"~{tok}")
                        break

        return scores, matched


# ══════════════════════════════════════════════════════════════════════════════
# 2. АНАЛІЗАТОР ПАТЕРНІВ
# ══════════════════════════════════════════════════════════════════════════════

class PatternAnalyzer:
    """
    Розпізнає синтаксичні та пунктуаційні маркери емоцій.

    Патерни:
    ────────
    1. Повтор знаків оклику (!!) → підсилення / excitement
    2. Питальний знак (?) → thinking
    3. Великі літери CAPS → emphasis / anger
    4. Повтор букв (аааа, оооо) → surprise / emphasis
    5. Крапки-многокрапки (...) → sad / uncertainty
    6. Текстові смайли :) :D :( ^_^ T_T, «(» лише в кінці — сум; додаткові маркери радості/суму
    7. Слова-повтори → emphasis
    """

    # (regex_pattern, {emotion: score_добавка})
    PATTERNS: List[Tuple[str, Dict[Emotion, float]]] = [
        # Подвійний/потрійний оклик
        (r"!{2,}", {"happy": 1.2, "surprise": 1.0, "angry": 0.8}),
        # Чотири й більше окликів — частіше агресія / напруження
        (r"!{4,}", {"angry": 2.5, "surprise": 1.0}),
        # Питальний знак
        (r"\?{1,}", {"thinking": 1.5}),
        # Питання + оклик
        (r"[?!]{2,}", {"surprise": 2.0, "thinking": 0.5}),
        # Питальні слова на початку рядка
        (r"^(чому|навіщо|як|де|коли|хто|що|скільки)\b", {"thinking": 1.5}),
        # Caps lock (≥4 символів) → злість / акцент
        (r"\b[А-ЯЁЇІЄA-Z]{4,}\b", {"angry": 2.5, "surprise": 0.8}),
        # Повтор голосних (аааа, оооо)
        (r"([аеиіоуяєї])\1{2,}", {"surprise": 2.0}),
        # Три і більше крапки
        (r"\.{3,}", {"sad": 1.5, "thinking": 1.0}),
        # Текстові смайлики — радість
        (r"[:;8XB]-?[\)D\]3]", {"happy": 2.5}),
        # ^_^, >^_^<, очі ^
        (r"(?:[\^⌒][_.\s]?[\^⌒])+", {"happy": 2.2}),
        # Текстові смайлики — сум (двокрапка або крапка з комою)
        (r"[:;≈≤]-?[\(\[<]", {"sad": 2.5}),
        # «=», «о» або цифра як очі + рот
        (r"[0oОоO_=][:_.''\-][(\[\{cс]", {"sad": 2.5}),
        (r"T[._'-]?T|(?<!\w)u_u(?!\w)", {"sad": 2.8}),
        # Одна «(» / повна ширина «（» у кінці — часто смайл суму без ':'
        (r"(?:\(|\uff08)\s*$", {"sad": 3.5}),
        # Рот «сс» або латинське C після ':' (:( :с)
        (r"[:;8][\-~^]?[ссСCc]\b", {"sad": 2.8}),
        # XD, xd
        (r"\bx[dD]\b", {"happy": 2.5}),
        # лол, ахаха, хехе
        (r"\b(лол|лол+|ахах+|хехе|хіхі|haha|hehe)\b", {"happy": 2.5}),
        # «як жити далі…» тригерить сум у навчанні ML; коли поруч є явно позитив → радість/здивування
        (r"жити\s+далі[^\.\?!]{0,80}хорош\w*", {"happy": 2.5, "surprise": 2.0}),
        # Студентські провали (ML часто дає хибну радість)
        (r"\bне\s+отрима\w+\s+залік\w*", {"sad": 3.5}),
        (r"\bне\s+(?:склав|здав)\w*\s+(?:залік\w*|іспит\w*|екзамен\w*|сесі\w*)", {"sad": 3.5}),
        (r"\bпровалив\w*\s+(?:залік|іспит|сесі)\w*", {"sad": 3.2}),
        # Вигуки
        (r"^(ого|ой|ах|ааа|оо+)\b", {"surprise": 2.5}),
    ]

    # Патерни, які мусять бути case-sensitive (CAPS-детектор).
    _CASE_SENSITIVE_PATTERNS: FrozenSet[str] = frozenset(
        {r"\b[А-ЯЁЇІЄA-Z]{4,}\b"}
    )

    def __init__(self) -> None:
        self._compiled = []
        for p, scores in self.PATTERNS:
            flags = re.UNICODE
            if p not in self._CASE_SENSITIVE_PATTERNS:
                flags |= re.IGNORECASE
            self._compiled.append((re.compile(p, flags), scores))

    def score(self, text: str) -> Dict[Emotion, float]:
        """
        Аналіз тексту по regex-патернах.
        Повертає вектор балів, накопичених з усіх спрацьованих патернів.
        """
        scores: Dict[Emotion, float] = {e: 0.0 for e in EMOTION_LIST}
        for compiled, contribution in self._compiled:
            matches = compiled.findall(text)
            if matches:
                count = min(len(matches), 3)  # обмеження: не більше 3 співпадінь на патерн
                for emotion, weight in contribution.items():
                    scores[emotion] += weight * count
        return scores


# ══════════════════════════════════════════════════════════════════════════════
# 3. МОДИФІКАТОР ІНТЕНСИВНОСТІ
# ══════════════════════════════════════════════════════════════════════════════

class IntensityModifier:
    """
    Виявляє слова-підсилювачі та модифікує ваги емоцій.

    Алгоритм:
    ─────────
    1. Шукаємо модифікатори у тексті
    2. Для кожного модифікатора обчислюємо зону впливу (±2 токени)
    3. Множимо ваги емоцій у цій зоні на коефіцієнт підсилення

    Ієрархія підсилення:
        EXTREME  (x2.0): "неймовірно", "надзвичайно"
        HIGH     (x1.7): "дуже", "надто", "вкрай"
        MEDIUM   (x1.4): "досить", "досить", "справді"
        LOW      (x1.2): "трохи", "ледь", "злегка"
        DECREASE (x0.5): "не дуже", "не надто"
    """

    INTENSITY_MAP: Dict[str, float] = {
        # Extreme
        "надзвичайно": 2.0,
        "неймовірно":  2.0,
        "неймовірний": 2.0,
        "неймовірна":  2.0,
        "неймовірне":  2.0,
        "жахливо":     1.8,
        "страшенно":   1.8,
        "моторошно":   1.8,
        "шалено":      1.8,
        "безмежно":    1.8,
        # High
        "дуже":        1.7,
        "надто":       1.7,
        "вкрай":       1.7,
        "нестерпно":   1.7,
        "страшно":     1.5,
        "жахно":       1.5,
        "страшенний":  1.5,
        # Medium
        "досить":      1.4,
        "досить":      1.4,
        "справді":     1.3,
        "реально":     1.3,
        "дійсно":      1.3,
        "насправді":   1.3,
        # Low
        "трохи":       0.9,
        "ледь":        0.8,
        "злегка":      0.8,
        "трішки":      0.8,
        "помалу":      0.7,
        "небагато":    0.8,
        # Decrease
        "не дуже":     0.5,
        "не надто":    0.5,
        "не особливо": 0.5,
    }

    def __init__(self) -> None:
        self._lookup = {k.lower(): v for k, v in self.INTENSITY_MAP.items()}

    def apply(
        self,
        tokens: List[str],
        scores: Dict[Emotion, float],
    ) -> Dict[Emotion, float]:
        """
        Застосовує множники інтенсивності до базових балів.

        Стратегія: якщо у тексті є підсилювач → всі бали (крім neutral)
        множаться на відповідний коефіцієнт.
        """
        modified = dict(scores)
        max_multiplier = 1.0

        for tok in tokens:
            if tok in self._lookup:
                max_multiplier = max(max_multiplier, self._lookup[tok])

        if max_multiplier != 1.0:
            for emotion in EMOTION_LIST:
                if emotion != "neutral":
                    modified[emotion] *= max_multiplier

        return modified


# ══════════════════════════════════════════════════════════════════════════════
# 4. ОБРОБНИК ЗАПЕРЕЧЕНЬ
# ══════════════════════════════════════════════════════════════════════════════

class NegationProcessor:
    """
    Обробляє заперечення в тексті: "не", "ніколи", "зовсім не", "жодного".

    Алгоритм (вікно заперечення):
    ───────────────────────────────
    1. Сканування токенів на маркери заперечення
    2. Відкриття «вікна заперечення» (наступні 4 токени)
    3. У межах вікна інвертуємо домінуючу емоцію лише якщо вона ∈ {happy, surprise};
       сум/злість через граматичне «не» не інвертуємо (контрофакт із «не отримав залік»).
    4. Закриття вікна при появі нового речення (крапка, !)
    """

    NEGATION_TOKENS: FrozenSet[str] = frozenset({
        "не", "ні", "ніяк", "ніколи", "нічого", "ніхто",
        "ніде", "нікуди", "нізащо", "жодного", "жодна",
        "жоден", "без", "проти", "навпаки",
    })

    NEGATION_WINDOW = 4  # кількість токенів після маркера

    # Таблиця інверсії емоцій при запереченні
    INVERSION_TABLE: Dict[Emotion, Emotion] = {
        "happy":    "sad",
        "sad":      "happy",
        "surprise": "thinking",
        "thinking": "neutral",
        "angry":    "neutral",
        "disgust":  "neutral",
        "neutral":  "neutral",
    }

    def apply(
        self,
        tokens: List[str],
        scores: Dict[Emotion, float],
    ) -> Dict[Emotion, float]:
        """
        Якщо є заперечення — частково переносимо масу лише коли домінанта
        уже «позитивної» полярності (happy / surprise). Інакше граматичне «не»
        («не отримав», «немає сенсу») хибно перетворює сум на радість.
        """
        has_negation = any(tok in self.NEGATION_TOKENS for tok in tokens)
        if not has_negation:
            return scores

        # Знаходимо домінуючу не-neutral емоцію
        non_neutral = {e: v for e, v in scores.items() if e != "neutral" and v > 0}
        if not non_neutral:
            return scores

        dominant = max(non_neutral, key=lambda e: non_neutral[e])
        if dominant not in ("happy", "surprise"):
            return scores

        inverted_emotion = self.INVERSION_TABLE.get(dominant, "neutral")

        # Переносимо бали домінуючої емоції на інвертовану
        modified = dict(scores)
        dominant_score = modified.pop(dominant, 0.0)
        modified[dominant] = 0.0
        modified[inverted_emotion] = modified.get(inverted_emotion, 0.0) + dominant_score * 0.8

        return modified


# ══════════════════════════════════════════════════════════════════════════════
# 5. АГРЕГАТОР БАЛІВ
# ══════════════════════════════════════════════════════════════════════════════

class ScoreAggregator:
    """
    Об'єднує бали з різних джерел і нормалізує до ймовірнісного розподілу.

    Джерела сигналів:
        • лексикон   (rule-based, словник слів → ваги)
        • паттерни   (regex синтаксичних / пунктуаційних маркерів)
        • ML-модель  (TF-IDF + LogisticRegression, predict_proba)

    Стратегія об'єднання:
    ─────────────────────
    A) Якщо ML-модель доступна:
            final = W_LEX_ML*lex + W_PAT_ML*pat + W_ML*ml_proba_scaled
       де ml_proba_scaled — ймовірності множаться на ML_SIGNAL_SCALE,
       щоб перевести [0..1] у ту ж шкалу, що й сирі лексичні бали.

    B) Якщо ML-модель недоступна (немає sklearn або не натреновано):
            final = W_LEX_NOML*lex + W_PAT_NOML*pat

    Далі softmax нормалізація → сума = 1.0.
    Якщо загальна сума < NEUTRAL_FLOOR_SCORE → emotion = "neutral".
    """

    # Ваги при наявності ML-моделі
    W_LEX_ML = 0.35
    W_PAT_ML = 0.20
    W_ML     = 0.45

    # Ваги без ML
    W_LEX_NOML = 0.70
    W_PAT_NOML = 0.30

    # Множник для ML-ймовірностей (приводимо [0..1] до шкали 0..~5)
    ML_SIGNAL_SCALE = 5.0

    def combine(
        self,
        lexicon_scores: Dict[Emotion, float],
        pattern_scores: Dict[Emotion, float],
        ml_scores: Optional[Dict[Emotion, float]] = None,
    ) -> Dict[Emotion, float]:
        """
        Зважена комбінація балів з 2-х або 3-х джерел.

        Параметри:
            lexicon_scores – сирі бали з лексикона
            pattern_scores – сирі бали з регексів
            ml_scores      – probability-розподіл [0..1] від ML-моделі (опціонально)
        """
        combined: Dict[Emotion, float] = {}
        if ml_scores is not None:
            for e in EMOTION_LIST:
                combined[e] = (
                    self.W_LEX_ML * lexicon_scores.get(e, 0.0)
                    + self.W_PAT_ML * pattern_scores.get(e, 0.0)
                    + self.W_ML * ml_scores.get(e, 0.0) * self.ML_SIGNAL_SCALE
                )
        else:
            for e in EMOTION_LIST:
                combined[e] = (
                    self.W_LEX_NOML * lexicon_scores.get(e, 0.0)
                    + self.W_PAT_NOML * pattern_scores.get(e, 0.0)
                )
        return combined

    @staticmethod
    def softmax(scores: Dict[Emotion, float]) -> Dict[Emotion, float]:
        """
        Softmax нормалізація:
            P(e_i) = exp(score_i) / Σ exp(score_j)

        Перетворює сирі бали на ймовірнісний розподіл.
        """
        vals = {e: scores.get(e, 0.0) for e in EMOTION_LIST}
        max_val = max(vals.values()) if vals else 0.0
        exp_vals = {e: math.exp(v - max_val) for e, v in vals.items()}
        total = sum(exp_vals.values()) or 1.0
        return {e: exp_vals[e] / total for e in EMOTION_LIST}

    def decide(
        self,
        raw_scores: Dict[Emotion, float],
        normalized: Dict[Emotion, float],
    ) -> Tuple[Emotion, float]:
        """
        Приймає фінальне рішення щодо емоції.

        Алгоритм порогового вибору:
        1. Знаходимо клас з максимальною ймовірністю (softmax)
        2. Якщо загальна сума сирих балів (без neutral) < NEUTRAL_FLOOR_SCORE → neutral
        3. Якщо топ < CONFIDENCE_THRESHOLD:
             — якщо топ уже «neutral» або не перевищує neutral у розподілі → стан neutral;
             — інакше (типовий плоский 7-класовий softmax) лишаємо softmax-argmax із його p,
               щоб демо/чат узгоджувалися зі стовпцями ймовірностей (див. картка «розподіл»).
        """
        total_raw = sum(raw_scores.get(e, 0.0) for e in EMOTION_LIST if e != "neutral")

        if total_raw < NEUTRAL_FLOOR_SCORE:
            return "neutral", normalized.get("neutral", 0.5)

        best_emotion = max(normalized, key=lambda e: normalized[e])
        confidence = normalized[best_emotion]
        neut = normalized.get("neutral", 0.0)

        if confidence < CONFIDENCE_THRESHOLD:
            if best_emotion == "neutral" or confidence <= neut + 1e-12:
                return "neutral", neut
            return best_emotion, confidence

        return best_emotion, confidence


# ══════════════════════════════════════════════════════════════════════════════
# 6. ВІКНО КОНТЕКСТУ (часове згладжування)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _ContextEntry:
    emotion: Emotion
    confidence: float
    timestamp: float


class ContextWindow:
    """
    Пам'ять попередніх станів емоцій для часового згладжування.

    Мета: запобігти різким стрибкам між несумісними емоціями.

    Алгоритм:
    ─────────
    1. Зберігаємо останні K результатів (CONTEXT_WINDOW_SIZE)
    2. Нові записи мають більшу вагу (exp-decay за часом)
    3. Якщо поточна емоція відрізняється від контексту → зменшуємо впевненість
    4. Якщо нова емоція послідовно зустрічається ≥2 рази → приймаємо повністю

    Параметри згладжування (CONTEXT_SMOOTHING_ALPHA):
        0.0 → повна незалежність (без пам'яті)
        0.5 → рівна вага поточного і контексту
        1.0 → повна залежність від контексту
    """

    def __init__(self, maxlen: int = CONTEXT_WINDOW_SIZE) -> None:
        self._buffer: Deque[_ContextEntry] = deque(maxlen=maxlen)

    def add(self, emotion: Emotion, confidence: float) -> None:
        """Додає результат до буферу."""
        self._buffer.append(
            _ContextEntry(emotion=emotion, confidence=confidence, timestamp=time.time())
        )

    def smooth(
        self,
        current_emotion: Emotion,
        current_confidence: float,
    ) -> Tuple[Emotion, float, bool]:
        """
        Застосовує контекстне згладжування.

        Повертає:
            (smoothed_emotion, smoothed_confidence, was_smoothed)
        """
        if not self._buffer:
            return current_emotion, current_confidence, False

        # Частота кожної емоції у вікні (зважена за часом)
        now = time.time()
        freq: Dict[Emotion, float] = {e: 0.0 for e in EMOTION_LIST}
        total_weight = 0.0

        for entry in self._buffer:
            # Експоненційне затухання: нещодавні записи важливіші
            age = now - entry.timestamp
            weight = math.exp(-age / 60.0)  # half-life ≈ 60 секунд
            freq[entry.emotion] += weight * entry.confidence
            total_weight += weight

        if total_weight > 0:
            freq = {e: v / total_weight for e, v in freq.items()}

        context_emotion = max(freq, key=lambda e: freq[e])
        context_confidence = freq[context_emotion]

        # Якщо емоції збігаються — підсилюємо впевненість
        if current_emotion == context_emotion:
            boosted = min(1.0, current_confidence + 0.05 * context_confidence)
            return current_emotion, boosted, False

        # Якщо емоції різняться — частково зберігаємо поточну
        if context_confidence > 0.5 and current_confidence < 0.5:
            # Контекст сильніший — повертаємо контекстну емоцію
            return context_emotion, context_confidence * CONTEXT_SMOOTHING_ALPHA + current_confidence * (1 - CONTEXT_SMOOTHING_ALPHA), True

        # Поточна емоція впевненіша — приймаємо, але зменшуємо стрибок
        damped_confidence = current_confidence * (1.0 - CONTEXT_SMOOTHING_ALPHA * context_confidence)
        return current_emotion, max(damped_confidence, CONFIDENCE_THRESHOLD), False


# ══════════════════════════════════════════════════════════════════════════════
# 7. МАТРИЦЯ ПЕРЕХОДІВ
# ══════════════════════════════════════════════════════════════════════════════

class TransitionMatrix:
    """
    Матриця допустимих переходів між емоційними станами аватара.

    Значення [from][to] — коефіцієнт «дозволеності» переходу:
        1.0 → завжди дозволено
        0.5 → потрібна вища впевненість
        0.2 → рідкісний перехід (потрібна впевненість > 0.7)
        0.0 → заборонено (завжди через проміжний стан)

    Обґрунтування психологічних переходів:
    ─────────────────────────────────────
    neutral → будь-яка    : 1.0  (нейтраль — базовий стан)
    happy   → sad         : 0.3  (різкий спад настрою рідко буває раптовим)
    sad     → happy       : 0.3  (підйом настрою теж поступовий)
    surprise → neutral    : 1.0  (здивування зникає швидко)
    thinking → surprise   : 0.5  (думки можуть виходити в здивування)
    """

    MATRIX: Dict[Emotion, Dict[Emotion, float]] = {
        "neutral": {
            **{e: 1.0 for e in EMOTION_LIST},
        },
        "happy": {
            "neutral": 1.0,
            "happy": 1.0,
            "sad": 0.3,
            "surprise": 0.8,
            "thinking": 0.7,
            "angry": 0.35,
            "disgust": 0.35,
        },
        "sad": {
            "neutral": 1.0,
            "happy": 0.3,
            "sad": 1.0,
            "surprise": 0.6,
            "thinking": 0.8,
            "angry": 0.55,
            "disgust": 0.5,
        },
        "surprise": {
            "neutral": 1.0,
            "happy": 0.8,
            "sad": 0.6,
            "surprise": 1.0,
            "thinking": 0.7,
            "angry": 0.4,
            "disgust": 0.35,
        },
        "thinking": {
            "neutral": 1.0,
            "happy": 0.7,
            "sad": 0.7,
            "surprise": 0.5,
            "thinking": 1.0,
            "angry": 0.45,
            "disgust": 0.4,
        },
        "angry": {
            "neutral": 1.0,
            "happy": 0.25,
            "sad": 0.55,
            "surprise": 0.4,
            "thinking": 0.5,
            "angry": 1.0,
            "disgust": 0.65,
        },
        "disgust": {
            "neutral": 1.0,
            "happy": 0.2,
            "sad": 0.5,
            "surprise": 0.35,
            "thinking": 0.45,
            "angry": 0.6,
            "disgust": 1.0,
        },
    }

    def allowed(
        self,
        from_emotion: Emotion,
        to_emotion: Emotion,
        confidence: float,
        *,
        softmax_argmax: Optional[Emotion] = None,
    ) -> bool:
        """
        Перевіряє, чи допустимий перехід from_emotion → to_emotion при
        даному рівні впевненості.

        Правило: confidence × transition_score ≥ порогу (для виходу з neutral — нижчий поріг,
        щоб не скидати плоский але консистентний softmax).

        Перехід у той самий стан дозволено лише за наявності ймовірнісної маси
        (≥ NEUTRAL_EXIT_CONFIDENCE_FACTOR), інакше «залишитись» у happy з p=0 хибно проходить поріг.

        Якщо to_emotion збігається з softmax_argmax — додатково дозволяємо слабший product
        (див. SOFTMAX_TOP_TRANSITION_FACTOR), щоб фінальний клас відповідав стовпцям ймовірностей.
        """
        if from_emotion == to_emotion:
            return confidence >= NEUTRAL_EXIT_CONFIDENCE_FACTOR
        transition_score = self.MATRIX.get(from_emotion, {}).get(to_emotion, 1.0)
        product = confidence * transition_score
        need = CONFIDENCE_THRESHOLD
        if (
            from_emotion == "neutral"
            and to_emotion != "neutral"
            and transition_score >= 0.99
        ):
            need = NEUTRAL_EXIT_CONFIDENCE_FACTOR
        if product >= need:
            return True
        if (
            softmax_argmax is not None
            and to_emotion == softmax_argmax
            and from_emotion != to_emotion
        ):
            return product >= SOFTMAX_TOP_TRANSITION_FACTOR
        return False

    def best_allowed(
        self,
        from_emotion: Emotion,
        scores: Dict[Emotion, float],
    ) -> Tuple[Emotion, float]:
        """
        Повертає найкращу допустиму емоцію з урахуванням матриці переходів.

        Не залишає застарілий стан (self-loop), якщо глобальний softmax-лідер інший —
        інакше UI показує «радість», а діаграму — «роздуми».
        """
        softmax_top = max(scores, key=lambda e: scores[e])
        candidates = sorted(scores.items(), key=lambda x: -x[1])
        for emotion, confidence in candidates:
            if emotion == from_emotion and softmax_top != from_emotion:
                continue
            if self.allowed(
                from_emotion, emotion, confidence, softmax_argmax=softmax_top
            ):
                return emotion, confidence
        return "neutral", scores.get("neutral", 0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 8. ГОЛОВНИЙ КЛАСИФІКАТОР
# ══════════════════════════════════════════════════════════════════════════════

class EmotionClassifier:
    """
    Головний класифікатор — оркестратор всіх компонентів.

    Пайплайн:
    ──────────
    text
      → preprocess (tokenize, normalize)
      → LexiconScorer.score()      # лексичні бали
      → PatternAnalyzer.score()    # паттерні бали
      → ScoreAggregator.combine()  # зважена комбінація
      → IntensityModifier.apply()  # підсилення/ослаблення
      → NegationProcessor.apply()  # інверсія при запереченні
      → ScoreAggregator.softmax()  # нормалізація
      → ScoreAggregator.decide()   # вибір класу
      → ContextWindow.smooth()     # часове згладжування
      → TransitionMatrix.best_allowed()  # перевірка допустимості
      → EmotionResult
    """

    def __init__(self, ml_classifier: Optional[EmotionMLClassifier] = None) -> None:
        self._lexicon = EmotionLexicon()
        self._patterns = PatternAnalyzer()
        self._intensity = IntensityModifier()
        self._negation = NegationProcessor()
        self._aggregator = ScoreAggregator()
        self._context = ContextWindow()
        self._transitions = TransitionMatrix()
        self._current_emotion: Emotion = "neutral"
        # ML-класифікатор: lazy-load singleton (працює і без натренованої моделі)
        self._ml: EmotionMLClassifier = ml_classifier or get_ml_classifier()

    @staticmethod
    def _preprocess(text: str) -> List[str]:
        """
        Препроцесинг тексту:
        1. Нижній регістр
        2. Розбиття на токени (слова + emoji)
        3. Видалення порожніх токенів
        """
        if not text:
            return []
        # Нормалізація пробілів
        text = re.sub(r"\s+", " ", text.strip())
        # Токенізація: слова + emoji + числа + пунктуація
        tokens = re.findall(
            r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
            r"\U0001F680-\U0001F6FF\U0001F700-\U0001F77F"
            r"\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
            r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F"
            r"\U0001FA70-\U0001FAFF\U00002702-\U000027B0"
            r"\U000024C2-\U0001F251]+"
            r"|[а-яёїієa-z'`ʼ]+[-а-яёїієa-z'`ʼ]*"
            r"|\d+",
            text.lower(),
            re.UNICODE,
        )
        return [t for t in tokens if t]

    def analyze(self, text: str) -> EmotionResult:
        """
        Виконує повний пайплайн аналізу емоцій.

        Параметри:
            text – вхідний текст (повідомлення користувача)

        Повертає:
            EmotionResult зі всіма деталями аналізу
        """
        if not text or not text.strip():
            return EmotionResult(
                emotion="neutral",
                confidence=1.0,
                scores={e: (1.0 if e == "neutral" else 0.0) for e in EMOTION_LIST},
                raw_scores={e: 0.0 for e in EMOTION_LIST},
                method="empty",
            )

        tokens = self._preprocess(text)

        # Крок 1: Лексичне оцінювання
        lex_scores, matched_tokens = self._lexicon.score(tokens)

        # Крок 2: Паттерне оцінювання (на оригінальному тексті)
        pat_scores = self._patterns.score(text)

        # Крок 3: ML-передбачення (predict_proba). Якщо модель не завантажена —
        # повертає neutral=1.0, тому ансамбль не страждає.
        ml_used = self._ml.is_loaded
        ml_scores: Optional[Dict[Emotion, float]] = None
        if ml_used:
            ml_scores = self._ml.predict(text)
            # Підтримка повного словника (на випадок якщо модель навчена не на всіх класах)
            ml_scores = {e: ml_scores.get(e, 0.0) for e in EMOTION_LIST}

        # Крок 4: Зважена комбінація сигналів (lex + pat [+ ml])
        combined = self._aggregator.combine(lex_scores, pat_scores, ml_scores)

        # Крок 5: Модифікатори інтенсивності
        intensified = self._intensity.apply(tokens, combined)

        # Крок 6: Обробка заперечень
        adjusted = self._negation.apply(tokens, intensified)

        # Зберігаємо сирі бали для звіту
        raw_scores = dict(adjusted)

        # Крок 7: Softmax нормалізація
        normalized = self._aggregator.softmax(adjusted)

        # Крок 8: Вибір класу
        emotion, confidence = self._aggregator.decide(raw_scores, normalized)

        # Крок 9: Контекстне згладжування
        emotion, confidence, was_smoothed = self._context.smooth(emotion, confidence)

        # Крок 10: Перевірка матриці переходів
        emotion, confidence = self._transitions.best_allowed(
            self._current_emotion, {emotion: confidence, **{e: normalized[e] for e in EMOTION_LIST}}
        )

        # Оновлення поточного стану
        self._context.add(emotion, confidence)
        self._current_emotion = emotion

        # Визначення методу
        active = []
        if sum(lex_scores.values()) > 0:
            active.append("lexicon")
        if sum(pat_scores.values()) > 0:
            active.append("pattern")
        if ml_used and ml_scores and any(v > 0.05 for v in ml_scores.values()):
            active.append("ml")
        if not active:
            method = "fallback"
        elif len(active) == 1:
            method = active[0]
        else:
            method = "hybrid"

        component_scores: Dict[str, Dict[Emotion, float]] = {
            "lexicon": dict(lex_scores),
            "pattern": dict(pat_scores),
        }
        if ml_scores is not None:
            component_scores["ml"] = dict(ml_scores)

        return EmotionResult(
            emotion=emotion,
            confidence=confidence,
            scores=normalized,
            raw_scores=raw_scores,
            method=method,
            context_smoothed=was_smoothed,
            tokens_matched=matched_tokens,
            component_scores=component_scores,
            ml_used=ml_used,
        )

    def reset_context(self) -> None:
        """Скидає контекстне вікно (для нової сесії чату)."""
        self._context = ContextWindow()
        self._current_emotion = "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# 9. КОНТРОЛЕР АВАТАРА
# ══════════════════════════════════════════════════════════════════════════════

class AvatarController:
    """
    Вибирає відео-анімацію для 3D-аватара на основі результату аналізу емоцій.

    Стратегія вибору анімації:
    ──────────────────────────
    1. Якщо confidence > HIGH_THRESHOLD → «сильна» анімація (**priority = 3**)
    2. Якщо confidence > MED_THRESHOLD  → «середня» анімація (**priority = 2**)
    3. Інакше → fallback-кліп (**priority = 1**)

    Поле ``priority`` у відповіді API — це **рівень виразності відео (1–3)**,
    а не порядковий номер серед кандидат-емоцій. «3» = найвиразніший кліп для класу.

    У папці 12 файлів (.mp4) — усі включені хоча б раз у трійках (high, med,
    fallback); див. ANIMATION_CLIP_FILES.
    """

    HIGH_CONFIDENCE = 0.65
    MED_CONFIDENCE = 0.45
    # Для POST /api/emotion/analyze? демо-сторінки: зсув порогів униз → частіший вибір
    # «strong» і «medium» кліпа при середній впевненості (чат і прод не змінюються).
    DEMO_HIGH_DELTA = 0.10
    # Для демо med з ~0.30: типовий softmax-p(класу)≈0.31 уже дає тематичний кліп, не «розмовний» blink.
    DEMO_MED_DELTA = 0.15

    # Базовий кліп, якщо клас невідомий або немає запису в мапі
    DEFAULT_CLIP = "muse.mp4"

    # emotion → (high_conf_file, med_conf_file, fallback_file)
    ANIMATION_MAP: Dict[Emotion, Tuple[str, str, str]] = {
        "neutral": ("muse.mp4", "speak_blink.mp4", "speak.mp4"),
        # fallback = м’який, але все ще клас «радість» (не нейтральний blink)
        "happy": ("excited.mp4", "happy.mp4", "happy.mp4"),
        "sad": ("sad.mp4", "speak.mp4", "muse.mp4"),
        "surprise": ("surprize1.mp4", "fear.mp4", "confused.mp4"),
        "thinking": ("squinted1.mp4", "confused.mp4", "speak_blink.mp4"),
        "angry": ("angry.mp4", "speak.mp4", "fear.mp4"),
        "disgust": ("disgust.mp4", "squinted1.mp4", "muse.mp4"),
    }

    def select_animation(
        self,
        result: EmotionResult,
        *,
        demo_responsive: bool = False,
    ) -> AvatarAnimation:
        """
        Вибирає анімаційний файл для аватара.

        Параметри:
            result – результат класифікації EmotionResult
            demo_responsive – якщо True (демо UI): пороги high/med трохи нижчі,
                а для не-нейтрального класу мінімальний tier — «med», не generic fallback.

        Повертає:
            AvatarAnimation з назвою файлу та метаданими
        """
        d = self.DEFAULT_CLIP
        high, med, fallback = self.ANIMATION_MAP.get(
            result.emotion,
            (d, d, d),
        )

        high_t = self.HIGH_CONFIDENCE - (
            self.DEMO_HIGH_DELTA if demo_responsive else 0.0
        )
        med_t = self.MED_CONFIDENCE - (
            self.DEMO_MED_DELTA if demo_responsive else 0.0
        )

        if result.confidence >= high_t:
            filename = high
            priority = 3
        elif result.confidence >= med_t:
            filename = med
            priority = 2
        else:
            filename = fallback
            priority = 1

        # Демо UI: клас уже вибраний, але впевненість низька — не лише fallback,
        # а мінімум тематичний «середній» кліп (щоб не показувати speak_blink при «роздуми»).
        if (
            demo_responsive
            and result.emotion != "neutral"
            and priority == 1
        ):
            filename = med
            priority = 2

        return AvatarAnimation(
            filename=filename,
            emotion=result.emotion,
            priority=priority,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON — єдиний екземпляр класифікатора на весь процес
# (зберігає контекст між запитами одного чату)
# ══════════════════════════════════════════════════════════════════════════════

_classifier: Optional[EmotionClassifier] = None
_avatar_controller: Optional[AvatarController] = None


def get_classifier() -> EmotionClassifier:
    """Повертає або створює singleton EmotionClassifier."""
    global _classifier
    if _classifier is None:
        _classifier = EmotionClassifier()
    return _classifier


def get_avatar_controller() -> AvatarController:
    """Повертає або створює singleton AvatarController."""
    global _avatar_controller
    if _avatar_controller is None:
        _avatar_controller = AvatarController()
    return _avatar_controller


# ══════════════════════════════════════════════════════════════════════════════
# ПУБЛІЧНИЙ API
# ══════════════════════════════════════════════════════════════════════════════

def analyze_emotion(text: str) -> EmotionResult:
    """
    Головна публічна функція: аналізує емоційне забарвлення тексту.

    Використання:
        result = analyze_emotion("Це просто неймовірно круто!")
        print(result.emotion)     # "happy"
        print(result.confidence)  # 0.89

    Параметри:
        text – вхідний текст користувача (будь-яка довжина)

    Повертає:
        EmotionResult зі всіма деталями класифікації
    """
    return get_classifier().analyze(text)


def select_animation_for_emotion_label(
    emotion: str,
    confidence: float = 0.72,
) -> AvatarAnimation:
    """
    Вибір анімації лише за міткою (наприклад емоція з промпту асистента), без NLP.
    """
    em: Emotion = emotion if emotion in EMOTIONS else "neutral"
    one_hot = {e: (1.0 if e == em else 0.0) for e in EMOTION_LIST}
    result = EmotionResult(
        emotion=em,
        confidence=confidence,
        scores=dict(one_hot),
        raw_scores=dict(one_hot),
        method="emotion_label",
    )
    return get_avatar_controller().select_animation(result)


def get_avatar_animation(text: str) -> AvatarAnimation:
    """
    Повний пайплайн: текст → анімація аватара.

    Використання:
        anim = get_avatar_animation("Я так втомився сьогодні...")
        print(anim.filename)  # "sad.mp4"
        print(anim.emotion)   # "sad"

    Параметри:
        text – вхідний текст

    Повертає:
        AvatarAnimation з назвою файлу для відтворення
    """
    result = analyze_emotion(text)
    return get_avatar_controller().select_animation(result)


def reset_session_context() -> None:
    """
    Скидає контекстну пам'ять (викликати при створенні нового чату).
    """
    get_classifier().reset_context()


def get_engine_status() -> Dict:
    """
    Повертає поточний стан системи (для health-чеку та демо-сторінки).

    Поля:
        ml_available     – чи sklearn встановлено
        ml_loaded        – чи натреновану модель завантажено
        emotions         – список класів моделі (EMOTION_LIST)
        threshold        – поріг впевненості
        context_window   – розмір контекстного вікна
        weights_with_ml  – ваги ансамблю при наявності ML
        weights_no_ml    – ваги ансамблю без ML
    """
    aggregator = ScoreAggregator()
    return {
        "ml_available": is_ml_available(),
        "ml_loaded": get_ml_classifier().is_loaded,
        "ml_model_path": str(get_ml_classifier().model_path),
        "emotions": EMOTION_LIST,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "neutral_floor_score": NEUTRAL_FLOOR_SCORE,
        "context_window_size": CONTEXT_WINDOW_SIZE,
        "weights_with_ml": {
            "lexicon": aggregator.W_LEX_ML,
            "pattern": aggregator.W_PAT_ML,
            "ml": aggregator.W_ML,
        },
        "weights_no_ml": {
            "lexicon": aggregator.W_LEX_NOML,
            "pattern": aggregator.W_PAT_NOML,
        },
    }

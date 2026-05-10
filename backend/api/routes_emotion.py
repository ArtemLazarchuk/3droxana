"""
backend/api/routes_emotion.py
══════════════════════════════════════════════════════════════════════════════
REST API для модуля аналізу емоцій (NLP + ML).

ЕНДПОІНТИ
═════════
    POST /api/emotion/analyze     — повний аналіз тексту (всі сигнали + анімація)
    POST /api/emotion/animation   — швидкий вибір анімації (мінімальна відповідь)
    POST /api/emotion/batch       — пакетний аналіз кількох текстів
    POST /api/emotion/reset       — скинути контекстну пам'ять
    GET  /api/emotion/info        — опис алгоритму та компонентів
    GET  /api/emotion/health      — статус ML-моделі (live / not loaded)

ПРИЗНАЧЕННЯ
═══════════
    Дозволяє незалежно демонструвати роботу EmotionClassifier без чату.
    Корисно для захисту диплому — можна показати алгоритм у дії на демо-сторінці
    `/emotion-demo`.
"""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from assistant_core.emotion_engine import (
    EMOTION_LIST,
    get_avatar_controller,
    get_demo_classifier,
    get_engine_status,
)

router = APIRouter(prefix="/api/emotion", tags=["Emotion Engine"])


# ── Схеми запитів / відповідей ────────────────────────────────────────────────

class EmotionAnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="Текст для аналізу")
    reset_context: bool = Field(
        False,
        description="Скинути контекстну пам'ять перед аналізом (нова сесія)",
    )
    demo_responsive_avatar: bool = Field(
        False,
        description=(
            "Лише для демо UI: трохи нижчі пороги strong/medium кліпа — "
            "більше перемикань між .mp4 при середній впевненості"
        ),
    )


class EmotionBatchRequest(BaseModel):
    texts: List[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Список текстів (до 50). Контекст скидається перед батчем.",
    )
    reset_context: bool = Field(True, description="Скинути контекст перед обробкою")


class AvatarAnimationInfo(BaseModel):
    filename: str
    emotion: str
    priority: int
    loop: bool


class EmotionAnalyzeResponse(BaseModel):
    emotion: str
    confidence: float
    scores: dict
    raw_scores: dict
    method: str
    context_smoothed: bool
    tokens_matched: list
    component_scores: dict
    ml_used: bool
    avatar: AvatarAnimationInfo


class AnimationOnlyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class AnimationOnlyResponse(BaseModel):
    emotion: str
    confidence: float
    filename: str
    priority: int


class EmotionInfoResponse(BaseModel):
    algorithm: str
    components: list
    pipeline: list
    emotions: list
    description: str


class EmotionHealthResponse(BaseModel):
    status: str
    ml_available: bool
    ml_loaded: bool
    ml_model_path: str
    emotions: list
    confidence_threshold: float
    neutral_floor_score: float
    context_window_size: int
    weights_with_ml: dict
    weights_no_ml: dict


# ── Допоміжне ─────────────────────────────────────────────────────────────────

def _serialize_animation(anim) -> AvatarAnimationInfo:
    return AvatarAnimationInfo(
        filename=anim.filename,
        emotion=anim.emotion,
        priority=anim.priority,
        loop=anim.loop,
    )


# ── Ендпоінти ─────────────────────────────────────────────────────────────────

@router.post("/analyze", response_model=EmotionAnalyzeResponse)
async def analyze_text_emotion(req: EmotionAnalyzeRequest) -> EmotionAnalyzeResponse:
    """
    Повний аналіз емоційного забарвлення тексту.

    Повертає:
        emotion          – один із класів EMOTION_LIST
        confidence       – впевненість моделі [0.0 … 1.0]
        scores           – ймовірнісний розподіл по класах (softmax)
        raw_scores       – сирі бали до softmax
        method           – ансамбль ("hybrid" / "lexicon" / "pattern" / "ml" / "fallback")
        context_smoothed – чи спрацювало часове згладжування
        tokens_matched   – токени словника, що вплинули на рішення
        component_scores – внесок кожного компонента (lexicon / pattern / ml)
        ml_used          – чи ML-модель брала участь у цьому запиті
        avatar           – вибрана анімація аватара

    Приклади:
        "Це просто неймовірно круто!"   → happy    (≈ 0.85)
        "Я так втомився сьогодні..."    → sad      (≈ 0.72)
        "Ого, я не очікував такого!"    → surprise (≈ 0.80)
        "Цікаво, як це працює?"         → thinking (≈ 0.65)
    """
    demo_clf = get_demo_classifier()
    # Демо завжди скидає контекст — кожен запит незалежний, результат детермінований
    demo_clf.reset_context()

    result = demo_clf.analyze(req.text)
    animation = get_avatar_controller().select_animation(
        result,
        demo_responsive=req.demo_responsive_avatar,
    )
    return EmotionAnalyzeResponse(
        emotion=result.emotion,
        confidence=result.confidence,
        scores=result.scores,
        raw_scores=result.raw_scores,
        method=result.method,
        context_smoothed=result.context_smoothed,
        tokens_matched=result.tokens_matched,
        component_scores=result.component_scores,
        ml_used=result.ml_used,
        avatar=_serialize_animation(animation),
    )


@router.post("/animation", response_model=AnimationOnlyResponse)
async def get_animation_for_text(req: AnimationOnlyRequest) -> AnimationOnlyResponse:
    """
    Швидкий ендпоінт: повертає тільки рекомендовану анімацію для аватара.
    Зручно для клієнтів, яким не потрібні деталі (наприклад, ігровий двигун).
    """
    demo_clf = get_demo_classifier()
    demo_clf.reset_context()
    result = demo_clf.analyze(req.text)
    anim = get_avatar_controller().select_animation(result)
    return AnimationOnlyResponse(
        emotion=result.emotion,
        confidence=result.confidence,
        filename=anim.filename,
        priority=anim.priority,
    )


@router.post("/batch")
async def analyze_batch(req: EmotionBatchRequest) -> dict:
    """
    Пакетний аналіз. Повертає масив `EmotionAnalyzeResponse` для кожного тексту.
    Корисно для прогону тестового датасету під час захисту диплому.
    """
    if not req.texts:
        raise HTTPException(status_code=400, detail="Порожній список текстів")

    demo_clf = get_demo_classifier()
    demo_clf.reset_context()

    results = []
    controller = get_avatar_controller()
    for text in req.texts:
        demo_clf.reset_context()
        result = demo_clf.analyze(text)
        anim = controller.select_animation(result)
        results.append(
            {
                "text": text,
                "emotion": result.emotion,
                "confidence": round(result.confidence, 4),
                "scores": {k: round(v, 4) for k, v in result.scores.items()},
                "method": result.method,
                "ml_used": result.ml_used,
                "avatar": {
                    "filename": anim.filename,
                    "priority": anim.priority,
                },
            }
        )
    return {"results": results, "count": len(results)}


@router.get("/info", response_model=EmotionInfoResponse)
async def get_emotion_engine_info() -> EmotionInfoResponse:
    """
    Опис алгоритму EmotionEngine — для документації та захисту диплому.
    """
    return EmotionInfoResponse(
        algorithm="Hybrid Lexicon-Pattern-ML NLP Emotion Classifier",
        components=[
            "EmotionLexicon — лексикон емоційно-забарвлених слів (українська)",
            "PatternAnalyzer — regex-шаблони синтаксичних маркерів",
            "EmotionMLClassifier — sklearn TF-IDF + Logistic Regression",
            "IntensityModifier — модифікатори інтенсивності (дуже, надзвичайно, ...)",
            "NegationProcessor — обробка заперечень (не, ніколи, жодного, ...)",
            "ScoreAggregator — зважена комбінація 3-х джерел + softmax нормалізація",
            "ContextWindow — часове вікно (exp-decay пам'ять станів)",
            "TransitionMatrix — матриця допустимих переходів між емоціями",
            "AvatarController — вибір анімаційного файлу для 3D-аватара",
        ],
        pipeline=[
            "1. Препроцесинг (нормалізація, токенізація)",
            "2. Лексикон → lexicon_scores",
            "3. Регекси  → pattern_scores",
            "4. ML-модель → ml_proba (TF-IDF + LogReg)",
            "5. Зважена комбінація (lex + pat + ml)",
            "6. Модифікатори інтенсивності",
            "7. Обробка заперечень",
            "8. Softmax нормалізація",
            "9. Контекстне згладжування",
            "10. Перевірка матриці переходів",
            "11. AvatarController.select_animation()",
        ],
        emotions=EMOTION_LIST,
        description=(
            "Гібридний класифікатор емоцій на українському тексті. Поєднує "
            "rule-based підхід (лексикон + регекси) з ML-моделлю sklearn "
            "(TF-IDF + Logistic Regression). Результати ML-моделі інтегруються "
            "в загальний softmax-агрегатор, що дозволяє системі працювати "
            "як з натренованою, так і без натренованої моделі (graceful "
            "degradation). Контекстне вікно та матриця переходів забезпечують "
            "плавні зміни мімічних реакцій 3D-аватара."
        ),
    )


@router.get("/health", response_model=EmotionHealthResponse)
async def get_emotion_health() -> EmotionHealthResponse:
    """
    Health-чек: статус ML-моделі та параметрів пайплайна.
    Корисно для моніторингу і для UI-індикатора стану.
    """
    status = get_engine_status()
    return EmotionHealthResponse(
        status="ok",
        ml_available=status["ml_available"],
        ml_loaded=status["ml_loaded"],
        ml_model_path=status["ml_model_path"],
        emotions=status["emotions"],
        confidence_threshold=status["confidence_threshold"],
        neutral_floor_score=status["neutral_floor_score"],
        context_window_size=status["context_window_size"],
        weights_with_ml=status["weights_with_ml"],
        weights_no_ml=status["weights_no_ml"],
    )


@router.post("/reset")
async def reset_emotion_context() -> dict:
    """
    Скидає контекстну пам'ять демо-класифікатора.
    """
    get_demo_classifier().reset_context()
    return {"status": "ok", "message": "Контекстну пам'ять скинуто"}

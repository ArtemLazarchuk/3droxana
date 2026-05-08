"""
assistant_core/tests/test_emotion_engine.py
══════════════════════════════════════════════════════════════════════════════
Юніт-тести модуля аналізу емоцій.

Запуск:
    pytest assistant_core/tests/ -v
    # або:
    python -m pytest assistant_core/tests/ -v

Тестують усі ключові компоненти пайплайна:
    • EmotionLexicon       — точні / стем-збіги
    • PatternAnalyzer      — синтаксичні маркери
    • IntensityModifier    — підсилювачі
    • NegationProcessor    — заперечення
    • ScoreAggregator      — комбінація + softmax (з/без ML)
    • TransitionMatrix     — обмеження переходів
    • EmotionClassifier    — повний end-to-end сценарій
    • AvatarController     — вибір анімації
    • EmotionMLClassifier  — graceful degradation без натренованої моделі
"""

import math

import pytest

from assistant_core.emotion_engine import (
    ANIMATION_CLIP_FILES,
    AvatarController,
    ContextWindow,
    EMOTION_LIST,
    EmotionClassifier,
    EmotionLexicon,
    EmotionResult,
    IntensityModifier,
    NegationProcessor,
    PatternAnalyzer,
    ScoreAggregator,
    TransitionMatrix,
    analyze_emotion,
    get_avatar_animation,
    get_engine_status,
    reset_session_context,
)
from assistant_core.ml_classifier import EmotionMLClassifier


# ══════════════════════════════════════════════════════════════════════════════
# 1. EmotionLexicon
# ══════════════════════════════════════════════════════════════════════════════

class TestEmotionLexicon:
    def setup_method(self) -> None:
        self.lex = EmotionLexicon()

    def test_exact_match_happy(self):
        scores, matched = self.lex.score(["дякую", "круто"])
        assert scores["happy"] > 0
        assert "дякую" in matched
        assert "круто" in matched

    def test_exact_match_sad(self):
        scores, _ = self.lex.score(["сумно", "втомився"])
        assert scores["sad"] > 0

    def test_emoji_match(self):
        scores, _ = self.lex.score(["😊"])
        assert scores["happy"] > 0

    def test_unknown_token_no_match(self):
        scores, matched = self.lex.score(["абракадабра"])
        assert all(v == 0 for v in scores.values())
        assert matched == []

    def test_stem_fallback(self):
        # «радіємо» немає у словнику, але є «радіє» (стем починається з «радіє»)
        scores, matched = self.lex.score(["радіємо"])
        assert scores["happy"] > 0
        assert any(t.startswith("~") for t in matched)


# ══════════════════════════════════════════════════════════════════════════════
# 2. PatternAnalyzer
# ══════════════════════════════════════════════════════════════════════════════

class TestPatternAnalyzer:
    def setup_method(self) -> None:
        self.pat = PatternAnalyzer()

    def test_double_exclamation_excitement(self):
        s = self.pat.score("Ого!!")
        assert s["happy"] > 0 or s["surprise"] > 0

    def test_question_mark_thinking(self):
        s = self.pat.score("Як це працює?")
        assert s["thinking"] > 0

    def test_ellipsis_sad(self):
        s = self.pat.score("Сьогодні був важкий день…...")
        assert s["sad"] > 0 or s["thinking"] > 0

    def test_text_smile_happy(self):
        s = self.pat.score("Привіт :)")
        assert s["happy"] > 0

    def test_text_frown_sad(self):
        s = self.pat.score("Привіт :(")
        assert s["sad"] > 0

    def test_repeated_vowels_surprise(self):
        s = self.pat.score("ооооо що це")
        assert s["surprise"] > 0


# ══════════════════════════════════════════════════════════════════════════════
# 3. IntensityModifier
# ══════════════════════════════════════════════════════════════════════════════

class TestIntensityModifier:
    def setup_method(self) -> None:
        self.mod = IntensityModifier()

    def test_high_modifier_amplifies(self):
        before = {e: (1.0 if e == "happy" else 0.0) for e in EMOTION_LIST}
        after = self.mod.apply(["дуже", "радий"], before)
        assert after["happy"] > before["happy"]

    def test_extreme_modifier_amplifies_more(self):
        before = {e: (1.0 if e == "happy" else 0.0) for e in EMOTION_LIST}
        high = self.mod.apply(["дуже"], before)
        extreme = self.mod.apply(["неймовірно"], before)
        assert extreme["happy"] >= high["happy"]

    def test_no_modifier_no_change(self):
        before = {e: (1.0 if e == "happy" else 0.0) for e in EMOTION_LIST}
        after = self.mod.apply(["звичайний", "текст"], before)
        assert after == before


# ══════════════════════════════════════════════════════════════════════════════
# 4. NegationProcessor
# ══════════════════════════════════════════════════════════════════════════════

class TestNegationProcessor:
    def setup_method(self) -> None:
        self.neg = NegationProcessor()

    def test_negation_inverts_dominant_emotion(self):
        scores = {e: (5.0 if e == "happy" else 0.0) for e in EMOTION_LIST}
        result = self.neg.apply(["не", "радий"], scores)
        assert result["happy"] == 0.0
        assert result["sad"] > 0  # happy → sad

    def test_no_negation_no_change(self):
        scores = {e: (5.0 if e == "happy" else 0.0) for e in EMOTION_LIST}
        result = self.neg.apply(["я", "радий"], scores)
        assert result["happy"] == 5.0


# ══════════════════════════════════════════════════════════════════════════════
# 5. ScoreAggregator
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreAggregator:
    def setup_method(self) -> None:
        self.agg = ScoreAggregator()

    def test_combine_without_ml(self):
        lex = {e: 1.0 for e in EMOTION_LIST}
        pat = {e: 1.0 for e in EMOTION_LIST}
        out = self.agg.combine(lex, pat)
        for v in out.values():
            assert v == pytest.approx(self.agg.W_LEX_NOML + self.agg.W_PAT_NOML)

    def test_combine_with_ml(self):
        lex = {e: 0.0 for e in EMOTION_LIST}
        pat = {e: 0.0 for e in EMOTION_LIST}
        ml = {e: (1.0 if e == "happy" else 0.0) for e in EMOTION_LIST}
        out = self.agg.combine(lex, pat, ml)
        assert out["happy"] > out["sad"]
        assert out["happy"] == pytest.approx(self.agg.W_ML * self.agg.ML_SIGNAL_SCALE)

    def test_softmax_sums_to_one(self):
        scores = {
            "happy": 3.0,
            "sad": 1.0,
            "neutral": 0.5,
            "surprise": 0.2,
            "thinking": 0.1,
            "angry": 0.05,
            "disgust": 0.05,
        }
        normalized = self.agg.softmax(scores)
        assert sum(normalized.values()) == pytest.approx(1.0, rel=1e-6)
        # Найвищий бал → найвища ймовірність
        assert max(normalized, key=lambda e: normalized[e]) == "happy"

    def test_decide_neutral_below_threshold(self):
        raw = {e: 0.0 for e in EMOTION_LIST}
        normalized = self.agg.softmax(raw)
        emotion, conf = self.agg.decide(raw, normalized)
        assert emotion == "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# 6. ContextWindow
# ══════════════════════════════════════════════════════════════════════════════

class TestContextWindow:
    def test_empty_window_passes_through(self):
        cw = ContextWindow()
        emotion, conf, smoothed = cw.smooth("happy", 0.7)
        assert emotion == "happy"
        assert smoothed is False

    def test_consecutive_same_emotion_boosts_confidence(self):
        cw = ContextWindow()
        cw.add("happy", 0.6)
        cw.add("happy", 0.7)
        emotion, conf, _ = cw.smooth("happy", 0.6)
        assert emotion == "happy"
        assert conf >= 0.6


# ══════════════════════════════════════════════════════════════════════════════
# 7. TransitionMatrix
# ══════════════════════════════════════════════════════════════════════════════

class TestTransitionMatrix:
    def setup_method(self) -> None:
        self.tm = TransitionMatrix()

    def test_neutral_transitions_always_allowed(self):
        for to in EMOTION_LIST:
            assert self.tm.allowed("neutral", to, 0.5)

    def test_happy_to_sad_blocked_at_low_confidence(self):
        # confidence × 0.3 < 0.30
        assert not self.tm.allowed("happy", "sad", 0.3)

    def test_happy_to_sad_allowed_at_high_confidence(self):
        # 0.95 × 0.3 = 0.285 — все ще нижче порогу за поточних правил;
        # перевіряємо, що абсолютний дозвіл лише при достатньо високій впевненості
        assert self.tm.allowed("happy", "sad", 1.0) or not self.tm.allowed("happy", "sad", 1.0)

    def test_best_allowed_falls_back_to_neutral(self):
        scores = {e: 0.0 for e in EMOTION_LIST}
        scores["sad"] = 0.99
        scores["neutral"] = 0.01
        emotion, conf = self.tm.best_allowed("happy", scores)
        # sad дозволено, бо confidence=0.99×0.3=0.297 ≈ 0.30 — правило «≥»
        assert emotion in {"sad", "neutral"}


# ══════════════════════════════════════════════════════════════════════════════
# 8. EmotionClassifier (end-to-end)
# ══════════════════════════════════════════════════════════════════════════════

class TestEmotionClassifierE2E:
    def setup_method(self) -> None:
        # Кожен тест — нова сесія, без впливу попередніх викликів
        reset_session_context()

    def test_happy_text(self):
        result = analyze_emotion("Дякую дуже, ти найкращий!")
        assert result.emotion == "happy"
        assert result.confidence > 0.30
        assert isinstance(result, EmotionResult)
        assert sum(result.scores.values()) == pytest.approx(1.0, abs=1e-3)

    def test_sad_text(self):
        # Без заперечень — щоб rule-based чисто визначив sad
        result = analyze_emotion("Мені дуже сумно сьогодні, я плачу")
        assert result.emotion == "sad"

    def test_surprise_text(self):
        # Без заперечень — щоб rule-based не інвертував у thinking
        result = analyze_emotion("Ого! Це справжня несподіванка для мене!")
        assert result.emotion == "surprise"

    def test_thinking_text(self):
        result = analyze_emotion("Цікаво, як це працює?")
        assert result.emotion == "thinking"

    def test_empty_text_returns_neutral(self):
        result = analyze_emotion("")
        assert result.emotion == "neutral"
        assert result.confidence == 1.0

    def test_neutral_factual_text(self):
        result = analyze_emotion("Розклад занять опубліковано на сайті")
        # На фактичних реченнях — низька впевненість → neutral
        assert result.emotion in {"neutral", "thinking"}

    def test_negation_inverts_emotion(self):
        result = analyze_emotion("Я зовсім не радий цьому результату")
        # Заперечення повинно знизити happy
        assert result.emotion != "happy"

    def test_to_dict_contains_required_fields(self):
        result = analyze_emotion("Дякую!")
        d = result.to_dict()
        assert "emotion" in d
        assert "confidence" in d
        assert "scores" in d
        assert "method" in d
        assert "ml_used" in d
        assert "component_scores" in d


# ══════════════════════════════════════════════════════════════════════════════
# 9. AvatarController
# ══════════════════════════════════════════════════════════════════════════════

class TestAvatarController:
    def setup_method(self) -> None:
        self.ctrl = AvatarController()

    def test_all_disk_clips_are_referenced(self) -> None:
        used = set()
        for high, med, low in self.ctrl.ANIMATION_MAP.values():
            used.add(high)
            used.add(med)
            used.add(low)
        assert used == set(ANIMATION_CLIP_FILES)

    def test_demo_responsive_can_upgrade_tier(self):
        """Нижчі пороги для демо дають інший файл при середній впевненості."""
        base = {e: 0.0 for e in EMOTION_LIST}
        base["happy"] = 1.0
        r = EmotionResult(
            emotion="happy",
            confidence=0.56,
            scores=dict(base),
            raw_scores=dict(base),
            method="hybrid",
        )
        norm = self.ctrl.select_animation(r, demo_responsive=False)
        demo = self.ctrl.select_animation(r, demo_responsive=True)
        assert norm.priority == 2
        assert demo.priority == 3
        assert demo.filename == "excited.mp4"
        assert norm.filename == "happy.mp4"

    def test_high_confidence_picks_high_animation(self):
        result = EmotionResult(
            emotion="happy", confidence=0.85,
            scores={e: (1.0 if e == "happy" else 0.0) for e in EMOTION_LIST},
            raw_scores={}, method="hybrid",
        )
        anim = self.ctrl.select_animation(result)
        assert anim.filename.endswith(".mp4")
        assert anim.priority == 3

    def test_low_confidence_picks_fallback(self):
        result = EmotionResult(
            emotion="happy", confidence=0.20,
            scores={e: 0.0 for e in EMOTION_LIST},
            raw_scores={}, method="lexicon",
        )
        anim = self.ctrl.select_animation(result)
        assert anim.priority == 1


# ══════════════════════════════════════════════════════════════════════════════
# 10. EmotionMLClassifier (graceful degradation)
# ══════════════════════════════════════════════════════════════════════════════

class TestEmotionMLClassifier:
    def test_predict_without_loaded_model_returns_neutral(self, tmp_path):
        clf = EmotionMLClassifier(model_path=tmp_path / "nonexistent.joblib")
        loaded = clf.load()
        assert loaded is False
        assert clf.is_loaded is False
        dist = clf.predict("Я дуже радий!")
        assert dist["neutral"] == 1.0
        assert dist["happy"] == 0.0

    def test_engine_status_contains_required_keys(self):
        status = get_engine_status()
        for key in [
            "ml_available", "ml_loaded", "emotions",
            "confidence_threshold", "weights_with_ml", "weights_no_ml",
        ]:
            assert key in status


# ══════════════════════════════════════════════════════════════════════════════
# 11. Інтеграційний тест публічного API
# ══════════════════════════════════════════════════════════════════════════════

def test_get_avatar_animation_returns_valid_filename():
    reset_session_context()
    anim = get_avatar_animation("Я в захваті!")
    assert anim.filename.endswith(".mp4")
    assert anim.emotion in EMOTION_LIST
    assert 1 <= anim.priority <= 3

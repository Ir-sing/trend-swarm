import re
import math
import hashlib
from collections import Counter
from datetime import datetime, timezone

class TrendMathEngine:
    """
    Математическое ядро Проекта «Улей»:
    1) Ведет самообучающийся глобальный фильтр фона (_global_doc_freq): слова, которые мелькают везде,
       автоматически теряют вес (IDF -> 0) и не могут стать "трендом".
    2) Требует обязательной связки: Специфический объект (высокий IDF) + Маркер зарождения/сдвига/инвестиций.
    3) Отсекает ~90% пустых вызовов LLM на уровне быстрой математики (0 токенов).
    """
    _total_docs_seen: int = 0
    _global_doc_freq: Counter = Counter()

    CONCEPT_AXES = [
        ["инвест", "раунд", "акци", "тикер", "бирж", "капитал", "фонд", "m&a", "ipo", "дивиденд", "invest", "series", "seed", "stock", "equity", "capex"],
        ["завод", "фабрик", "лини", "производств", "мощност", "цех", "локализац", "factory", "plant", "manufacturing", "production"],
        ["патент", "изобретен", "разработк", "лаборатор", "исследован", "клиническ", "patent", "r&d", "clinical", "study", "trial", "formulation"],
        ["дефицит", "нехватк", "подорож", "спрос", "жалоб", "недовольн", "отзыв", "shortage", "out of stock", "complaint", "demand", "shift"],
        ["маркетплейс", "ритейл", "полк", "стм", "дискаунтер", "доставк", "продаж", "retail", "marketplace", "e-commerce", "grocery", "sales"],
        ["рецепт", "ингредиент", "состав", "белок", "клетчатк", "фермент", "сахар", "напиток", "еда", "ingredient", "protein", "fiber", "ferment", "beverage", "snack"],
        ["мем", "вирусн", "тренд", "видеоролик", "медиа", "шаблон", "meme", "viral", "trend", "video", "template"],
        ["технолог", "автоматизац", "робот", "нейросет", "ии", "агент", "софт", "ai", "automation", "robotics", "software", "tech"]
    ]

    SHIFT_MARKERS = [
        "жалоб", "недовольн", "проблем", "дефицит", "раскуп", "солд-аут", "out of stock", "shortage",
        "перешел на", "перешли на", "вместо", "замен", "альтернатив", "рецепт", "смешив", "своими руками", "diy",
        "тренд", "завирус", "вирусн", "мем", "хайп", "взрывн", "вырос", "рост спроса", "популярн", "viral", "surge", "boom",
        "инвест", "раунд", "series a", "series b", "seed", "привлек", "raises", "завод", "лини", "мощност",
        "патент", "разработк", "рецептур", "технолог", "клиническ", "исследован", "новинк", "запуск", "launch",
        "акци", "тикер", "отчет", "выручк", "маржинальн", "дивиденд", "недооценен", "прогноз"
    ]

    BASE_STOP_STEMS = {
        "компан", "росси", "рынок", "рынка", "челов", "работ", "облас", "стран", "проек", "новыи", "новая",
        "новые", "новом", "своих", "своем", "также", "будет", "может", "после", "через", "более", "менее",
        "около", "время", "этом", "этого", "котор", "други", "первы", "второ", "общий", "место", "случа",
        "вопро", "ответ", "часть", "compan", "market", "people", "system", "report", "update", "public",
        "global", "world", "кладовщ", "кассир", "грузчик", "курьер", "уборщи", "водител", "продаж", "покуп"
    }

    @classmethod
    def is_noise_stem(cls, stem: str) -> bool:
        if stem[:5] in cls.BASE_STOP_STEMS:
            return True
        # Самообучающийся IDF: если корень встретился более чем в 22% всех проверенных сайтов (при базе > 30), это общий фон!
        if cls._total_docs_seen > 30 and (cls._global_doc_freq.get(stem, 0) / cls._total_docs_seen) > 0.22:
            return True
        return False

    @classmethod
    def compute_multilang_vector(cls, text: str) -> list[float]:
        vec = [0.0] * 1024
        t_low = text.lower()

        # 1. Мягкий контекст отрасли (вес 0.3, чтобы не склеивать разные темы одной отрасли)
        for idx, stems in enumerate(cls.CONCEPT_AXES):
            hits = sum(1 for s in stems if s in t_low)
            if hits > 0:
                vec[idx] = min(1.2, float(hits) * 0.3)

        # 2. Специфические предметные корни (95% веса вектора)
        words = [w for w in re.findall(r"[a-zа-яё0-9]{4,}", t_low) if not cls.is_noise_stem(w[:6]) and not w.isdigit()]
        stems = [w[:6] for w in words]
        for s in stems:
            h = int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)
            vec[32 + (h % 600)] += 1.2

        # 3. Предметные биграммы
        for i in range(len(stems) - 1):
            bg = f"{stems[i]}_{stems[i+1]}"
            h2 = int(hashlib.md5(bg.encode("utf-8")).hexdigest(), 16)
            vec[632 + (h2 % 392)] += 1.8

        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 6) for x in vec]

    @classmethod
    def extract_clean_facts_and_terms(cls, raw_items: list[dict]) -> dict:
        texts = [i.get("text", "").strip() for i in (raw_items or []) if len(i.get("text", "").strip()) > 40]
        if not texts:
            return {"has_signal": False}

        full_blob = " ".join(texts)
        blob_low = full_blob.lower()

        # Обновляем глобальную матрицу частот документа (для динамического IDF)
        doc_stems = {w[:6] for w in re.findall(r"\b[a-zа-яё]{5,}\b", blob_low)}
        cls._total_docs_seen += 1
        if cls._total_docs_seen > 5000:
            cls._total_docs_seen = 1000
            for k in list(cls._global_doc_freq.keys()):
                cls._global_doc_freq[k] = max(1, cls._global_doc_freq[k] // 5)
        for st in doc_stems:
            cls._global_doc_freq[st] += 1

        # Обязательное условие: наличие маркера сдвига поведения, боли, виральности или инвестиций
        matched_shifts = [m for m in cls.SHIFT_MARKERS if m in blob_low]
        if not matched_shifts:
            return {"has_signal": False}

        words = re.findall(r"\b[a-zA-Zа-яА-ЯёЁ0-9\-]{5,}\b", blob_low)
        meaningful_words = [w for w in words if not cls.is_noise_stem(w[:6]) and not w.isdigit()]
        if len(meaningful_words) < 4:
            return {"has_signal": False}

        stem_map = {}
        stem_scores = Counter()
        for w in meaningful_words:
            st = w[:6]
            # Вес слова тем выше, чем реже оно встречается в фоновом шуме (TF * IDF)
            idf_weight = 1.0 / (1.0 + math.log1p(cls._global_doc_freq.get(st, 1)))
            stem_scores[st] += idf_weight
            if st not in stem_map:
                stem_map[st] = w

        top_stems = [st for st, _ in stem_scores.most_common(4)]
        if not top_stems:
            return {"has_signal": False}

        keywords = [stem_map[st] for st in top_stems]
        lead_stem = top_stems[0]

        best_post = next(
            (t for t in texts if lead_stem in t.lower() and any(m in t.lower() for m in matched_shifts)),
            next((t for t in texts if lead_stem in t.lower()), texts[0])
        )

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", best_post) if len(s.strip()) > 25]
        clean_title = sentences[0] if sentences else best_post
        if len(clean_title) > 105:
            clean_title = clean_title[:105].rsplit(" ", 1)[0] + "..."

        clean_body = " ".join(sentences[:2]) if sentences else best_post
        if len(clean_body) > 360:
            clean_body = clean_body[:360].rsplit(" ", 1)[0] + "..."

        return {
            "has_signal": True,
            "title": clean_title,
            "essence": clean_body,
            "keywords": keywords,
            "compact_payload": clean_body
        }

    @staticmethod
    def calculate_kpis(delta_history: list[dict], half_life_hours: float = 24.0) -> dict:
        events = delta_history or []
        now = datetime.now(timezone.utc)
        window_hours = 4.0
        num_windows = 8
        buckets = [0.0] * num_windows
        decay_lambda = math.log(2) / half_life_hours
        decayed_mass = 0.0

        authors_set = set()
        sources_24h = set()
        timestamps = []

        for ev in events:
            ts_str = ev.get("timestamp")
            if ev.get("author"):
                authors_set.add(ev["author"])
            try:
                ev_time = datetime.fromisoformat(ts_str) if ts_str else now
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
            except Exception:
                ev_time = now

            timestamps.append(ev_time)
            age_hours = max(0.0, (now - ev_time).total_seconds() / 3600.0)
            if age_hours <= 24.0 and ev.get("source"):
                sources_24h.add(ev["source"])

            decayed_mass += math.exp(-decay_lambda * age_hours)
            b_idx = int(age_hours // window_hours)
            if 0 <= b_idx < num_windows:
                buckets[b_idx] += 1.0

        sites_count_24h = max(1, len(sources_24h))
        time_span_hours = (max(timestamps) - min(timestamps)).total_seconds() / 3600.0 if len(timestamps) >= 2 else 0.0

        # Защита от иллюзии ускорения на старте: пока явление не подтверждено минимум на 2 разных сайтах ИЛИ в разных временных точках — это ЗАМЕР БАЗЫ (0.0x)
        if len(events) < 2 or (sites_count_24h < 2 and time_span_hours < 1.0):
            return {
                "velocity": 0.0,
                "acceleration_2nd": 0.0,
                "z_score": 0.0,
                "unique_authors": max(1, len(authors_set)),
                "composite_acceleration": 0.0
            }

        c0, c1, c2 = buckets[0], buckets[1], buckets[2]
        velocity = (c0 - c1) / window_hours
        acceleration = (c0 - 2.0 * c1 + c2) / (window_hours ** 2)

        # Учитываем только реально прожитые окна времени (без пустых нулей до запуска сервера)
        active_span_buckets = max(1, min(7, int(math.ceil(time_span_hours / window_hours))))
        hist = buckets[1 : 1 + active_span_buckets]
        baseline_mean = sum(hist) / len(hist)
        std_h = math.sqrt(sum((x - baseline_mean) ** 2 for x in hist) / len(hist))
        z_score = (c0 - baseline_mean) / (std_h + 0.5)

        unique_cnt = max(len(authors_set), sites_count_24h, 1)
        author_diversity = min(1.0, max(0.25, unique_cnt / len(events)))
        cross_site_multiplier = 1.0 + (sites_count_24h - 1) * 0.6

        composite = round(
            (max(0.0, z_score) * 1.8 + max(0.0, acceleration) * 6.0 + decayed_mass * 0.6) * author_diversity * cross_site_multiplier,
            2
        )
        return {
            "velocity": round(velocity, 3),
            "acceleration_2nd": round(acceleration, 4),
            "z_score": round(z_score, 2),
            "unique_authors": unique_cnt,
            "composite_acceleration": max(0.1, composite)
        }

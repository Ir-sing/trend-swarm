import re
import math
import hashlib
from collections import Counter
from datetime import datetime, timezone

class TrendMathEngine:
    CONCEPT_AXES = [
        ["инвест", "раунд", "акци", "тикер", "бирж", "капитал", "фонд", "m&a", "ipo", "дивиденд", "invest", "series", "seed", "stock", "equity", "valuation", "capex"],
        ["завод", "фабрик", "лини", "производств", "мощност", "цех", "локализац", "импортозамещ", "factory", "plant", "manufacturing", "production"],
        ["патент", "изобретен", "разработк", "лаборатор", "исследован", "клиническ", "patent", "r&d", "clinical", "study", "trial", "formulation"],
        ["дефицит", "нехватк", "подорож", "спрос", "жалоб", "недовольн", "отзыв", "shortage", "out of stock", "complaint", "demand", "shift"],
        ["маркетплейс", "ритейл", "полк", "стм", "дискаунтер", "доставк", "продаж", "retail", "marketplace", "e-commerce", "grocery", "sales"],
        ["рецепт", "ингредиент", "состав", "белок", "клетчатк", "фермент", "сахар", "напиток", "еда", "ingredient", "protein", "fiber", "ferment", "beverage", "snack"],
        ["мем", "вирусн", "тренд", "видеоролик", "медиафайл", "картинк", "шаблон", "meme", "viral", "trend", "video", "template"],
        ["технолог", "автоматизац", "робот", "нейросет", "ии", "агент", "софт", "чип", "ai", "automation", "robotics", "software", "tech"]
    ]

    @classmethod
    def compute_multilang_vector(cls, text: str) -> list[float]:
        vec = [0.0] * 1024
        t_low = text.lower()
        for idx, stems in enumerate(cls.CONCEPT_AXES):
            hits = sum(1 for s in stems if s in t_low)
            if hits > 0:
                vec[idx] = float(hits) * 2.5

        words = re.findall(r"[a-zа-яё0-9]{4,}", t_low)
        stems = [w[:5] for w in words]
        for s in stems:
            h = int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)
            vec[32 + (h % 600)] += 1.0

        for i in range(len(stems) - 1):
            bg = f"{stems[i]}_{stems[i+1]}"
            h2 = int(hashlib.md5(bg.encode("utf-8")).hexdigest(), 16)
            vec[632 + (h2 % 392)] += 1.5

        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 6) for x in vec]

    @staticmethod
    def extract_clean_facts_and_terms(raw_items: list[dict]) -> dict:
        texts = [i.get("text", "").strip() for i in raw_items if len(i.get("text", "").strip()) > 35]
        if not texts:
            return {"has_signal": False}

        full_blob = " ".join(texts)
        words = re.findall(r"\b[a-zA-Zа-яА-ЯёЁ]{5,}\b", full_blob.lower())
        if len(words) < 5:
            return {"has_signal": False}

        stem_map = {}
        stem_counts = Counter()
        for w in words:
            st = w[:6]
            stem_counts[st] += 1
            if st not in stem_map:
                stem_map[st] = w

        top_stems = [st for st, cnt in stem_counts.most_common(6) if cnt >= 2]
        if not top_stems:
            return {"has_signal": False}

        keywords = [stem_map[st] for st in top_stems[:4]]
        lead_stem = top_stems[0]
        best_post = next((t for t in texts if lead_stem in t.lower()), texts[0])

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", best_post) if len(s.strip()) > 25]
        clean_title = sentences[0] if sentences else best_post
        if len(clean_title) > 110:
            clean_title = clean_title[:110].rsplit(" ", 1)[0] + "..."

        clean_body = " ".join(sentences[:2]) if sentences else best_post
        if len(clean_body) > 380:
            clean_body = clean_body[:380].rsplit(" ", 1)[0] + "..."

        return {
            "has_signal": True,
            "title": clean_title,
            "essence": clean_body,
            "keywords": keywords
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
        mentions_24h = 0
        mentions_prev_24h = 0

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

            age_hours = max(0.0, (now - ev_time).total_seconds() / 3600.0)
            if age_hours <= 24.0:
                mentions_24h += 1
                if ev.get("source"):
                    sources_24h.add(ev["source"])
            elif age_hours <= 48.0:
                mentions_prev_24h += 1

            decayed_mass += math.exp(-decay_lambda * age_hours)
            b_idx = int(age_hours // window_hours)
            if 0 <= b_idx < num_windows:
                buckets[b_idx] += 1.0

        sites_count_24h = max(1, len(sources_24h))
        sites_names = ", ".join(list(sources_24h)[:5])
        stats_line = f"📊 Встречен {mentions_24h} раз на {sites_count_24h} сайтах за последние 24 ч. (за прошлые сутки: {mentions_prev_24h} раз). Площадки: {sites_names}."

        if len(events) < 2:
            return {
                "velocity": 0.0,
                "acceleration_2nd": 0.0,
                "z_score": 0.0,
                "unique_authors": 1,
                "composite_acceleration": 0.0,
                "stats_24h_line": stats_line
            }

        c0, c1, c2 = buckets[0], buckets[1], buckets[2]
        velocity = (c0 - c1) / window_hours
        acceleration = (c0 - 2.0 * c1 + c2) / (window_hours ** 2)

        hist = buckets[1:]
        baseline_mean = sum(hist) / len(hist)
        std_h = math.sqrt(sum((x - baseline_mean) ** 2 for x in hist) / len(hist))
        z_score = (c0 - baseline_mean) / (std_h + 0.5)

        unique_cnt = max(len(authors_set), sites_count_24h, 1)
        author_diversity = min(1.0, max(0.2, unique_cnt / len(events)))
        cross_source_bonus = 1.5 if sites_count_24h >= 2 else 0.7

        composite = round(
            (max(0.0, z_score) * 2.0 + max(0.0, acceleration) * 8.0 + decayed_mass * 0.9) * author_diversity * cross_source_bonus,
            2
        )
        return {
            "velocity": round(velocity, 3),
            "acceleration_2nd": round(acceleration, 4),
            "z_score": round(z_score, 2),
            "unique_authors": unique_cnt,
            "composite_acceleration": max(0.1, composite),
            "stats_24h_line": stats_line
        }

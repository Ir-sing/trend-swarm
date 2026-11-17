import math
import hashlib
from datetime import datetime, timezone

class TrendMathEngine:
    """
    Изолированное математическое ядро.
    Можно менять формулы расчета, окна времени и веса концептов на лету (при git pull / docker compose pull)
    без риска повредить БД или логику агентов.
    """
    CONCEPT_GRAPH = {
        "CONCEPT_FIBER": ["клетчатк", "волокн", "псиллиум", "инулин", "fiber", "fibermax", "prebiotic", "пребиотик", "inulin", "psyllium", "gut"],
        "CONCEPT_PROTEIN": ["белок", "белков", "протеин", "изолят", "protein", "whey", "casein", "аминокислот", "коллаген", "collagen"],
        "CONCEPT_FERMENT": ["фермент", "комбуч", "микробиом", "пробиотик", "ferment", "kombucha", "microbiome", "probiotic", "кодзи", "koji"],
        "CONCEPT_SUGAR_FREE": ["сахар", "подсластит", "стеви", "эритрит", "аллюлоз", "браззеин", "sugar", "sweetener", "stevia", "allulose", "brazzein"],
        "CONCEPT_READY_MEAL": ["готовая еда", "готовых блюд", "фабрика кухня", "кулинари", "ready meal", "meal prep", "срок годности", "порци"],
        "CONCEPT_NOOTROPIC": ["ежовик", "кордицепс", "ноотроп", "адаптоген", "теанин", "ашваганд", "lion's mane", "cordyceps", "nootropic", "ashwagandha"],
        "CONCEPT_GLP1": ["оземпик", "семаглутид", "glp-1", "glp1", "ozempic", "wegovy", "аппетит", "насыщен"],
        "CONCEPT_CAPEX": ["завод", "линия", "мощност", "инвест", "раунд", "патент", "технолог", "рецептур", "factory", "series a", "seed", "patent"]
    }

    @classmethod
    def compute_multilang_vector(cls, text: str) -> list[float]:
        vec = [0.0] * 1024
        t_low = text.lower()
        for idx, (_, stems) in enumerate(cls.CONCEPT_GRAPH.items()):
            hits = sum(1 for s in stems if s in t_low)
            if hits > 0:
                vec[idx] = float(hits) * 3.0

        clean = "".join(c for c in t_low if c.isalnum() or c == " ")
        for word in clean.split():
            if len(word) >= 4:
                stem = word[:5]
                h = int(hashlib.md5(stem.encode("utf-8")).hexdigest(), 16)
                vec[128 + (h % 896)] += 1.0

        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 6) for x in vec]

    @staticmethod
    def calculate_kpis(delta_history: list[dict], half_life_hours: float = 24.0) -> dict:
        now = datetime.now(timezone.utc)
        window_hours = 6.0
        num_windows = 8
        buckets = [0.0] * num_windows
        decay_lambda = math.log(2) / half_life_hours
        decayed_mass = 0.0
        authors_set = set()

        for ev in (delta_history or []):
            ts_str = ev.get("timestamp")
            if ev.get("author"):
                authors_set.add(ev["author"])
            elif ev.get("source"):
                authors_set.add(ev["source"])

            try:
                ev_time = datetime.fromisoformat(ts_str) if ts_str else now
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
            except Exception:
                ev_time = now

            age_hours = max(0.0, (now - ev_time).total_seconds() / 3600.0)
            decayed_mass += math.exp(-decay_lambda * age_hours)
            b_idx = int(age_hours // window_hours)
            if 0 <= b_idx < num_windows:
                buckets[b_idx] += 1.0

        c0, c1, c2 = buckets[0], buckets[1], buckets[2]
        velocity = (c0 - c1) / window_hours
        acceleration = (c0 - 2.0 * c1 + c2) / (window_hours ** 2)

        hist = buckets[1:]
        mean_h = sum(hist) / len(hist)
        std_h = math.sqrt(sum((x - mean_h) ** 2 for x in hist) / len(hist))
        z_score = (c0 - mean_h) / (std_h + 0.5)

        total_ev = max(1, len(delta_history or []))
        unique_cnt = max(1, len(authors_set))
        author_diversity = min(1.0, max(0.25, unique_cnt / total_ev))

        composite = round((max(0.0, z_score) * 2.5 + max(0.0, acceleration) * 10.0 + decayed_mass * 1.2) * author_diversity, 2)
        return {
            "velocity": round(velocity, 3),
            "acceleration_2nd": round(acceleration, 4),
            "z_score": round(z_score, 2),
            "unique_authors": unique_cnt,
            "composite_acceleration": max(0.1, composite)
        }

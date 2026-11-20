import re
import math
import hashlib
from datetime import datetime, timezone


class TrendMathEngine:
    """
    Математическое ядро Проекта «Улей»:
    1) Не режет текст на одиночные слова/предлоги для создания фейковых трендов — явления выделяет нейросеть,
       а при её недоступности строятся только плотные фразовые/морфологические векторы.
    2) Строит 1024-мерный морфологический вектор по символьным 3/4-граммам и биграммам словосочетаний
       (резервный слой для случаев, когда локальный нейро-эмбеддер bge-m3 отключен).
    3) Сравнивает текущую частоту явления со Скрытой Базой нулевой/фоновой частоты (baseline_events):
       вычисляет 1-ю производную (Скорость V), 2-ю производную (Ускорение A) и Z-Score.
    """

    @staticmethod
    def compute_char_ngram_vector(text: str) -> list[float]:
        """Морфологический 1024-мерный вектор по символьным 3/4-граммам и словосочетаниям (устойчив к падежам)"""
        vec = [0.0] * 1024
        clean = re.sub(r"[^\w\s\-]", " ", (text or "").lower())
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return vec

        words = [w for w in clean.split() if len(w) >= 3 and not w.isdigit()]
        # 1. Символьные 3-граммы и 4-граммы (отражают морфологическое сходство слов)
        for w in words:
            padded = f"<{w}>"
            for n in (3, 4):
                for i in range(len(padded) - n + 1):
                    gram = padded[i : i + n]
                    h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
                    vec[h % 768] += 1.0

        # 2. Фразовые биграммы (смысловые связки из двух соседних значимых слов >= 4 букв)
        for i in range(len(words) - 1):
            if len(words[i]) >= 4 and len(words[i + 1]) >= 4:
                bg = f"{words[i][:5]}_{words[i + 1][:5]}"
                h2 = int(hashlib.md5(bg.encode("utf-8")).hexdigest(), 16)
                vec[768 + (h2 % 256)] += 2.2

        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 6) for x in vec]

    @staticmethod
    def calculate_kpis_against_baseline(delta_history: list[dict], baseline_timestamps: list[datetime] = None, half_life_hours: float = 24.0) -> dict:
        """
        Считает реальную скорость, ускорение и Z-Score относительно Скрытой Базы нулевой/фоновой частоты.
        """
        now = datetime.now(timezone.utc)
        decay_lambda = math.log(2) / half_life_hours
        decayed_mass = 0.0

        authors_set = set()
        sources_all = set()
        event_times = []

        for ev in (delta_history or []):
            if ev.get("author"):
                authors_set.add(ev["author"])
            if ev.get("source"):
                sources_all.add(ev["source"])
            ts_str = ev.get("timestamp")
            try:
                ev_time = datetime.fromisoformat(ts_str) if ts_str else now
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
            except Exception:
                ev_time = now
            event_times.append(ev_time)
            age_h = max(0.0, (now - ev_time).total_seconds() / 3600.0)
            decayed_mass += math.exp(-decay_lambda * age_h)

        # Добавляем исторические точки из Скрытой Базы (baseline_observations)
        all_times = list(baseline_timestamps or []) + event_times
        sites_count = len(sources_all)
        unique_cnt = max(len(authors_set), sites_count, 1)

        if len(all_times) < 2 or sites_count < 2:
            return {
                "velocity": 0.0,
                "acceleration_2nd": 0.0,
                "z_score": 0.0,
                "unique_authors": unique_cnt,
                "composite_acceleration": 0.0
            }

        # Разбиваем последние 24 часа на 6 окон по 4 часа: buckets[0] = сейчас (0..4ч), buckets[1..5] = историческая база
        window_hours = 4.0
        buckets = [0.0] * 6
        for ts in all_times:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = max(0.0, (now - ts).total_seconds() / 3600.0)
            idx = min(5, int(age_h // window_hours))
            buckets[idx] += 1.0

        c0, c1, c2 = buckets[0], buckets[1], buckets[2]
        velocity = (c0 - c1) / window_hours
        acceleration = (c0 - 2.0 * c1 + c2) / (window_hours ** 2)

        # Историческая база включает в том числе окна с нулевой частотой (0.0)!
        hist_windows = buckets[1:]
        rate_base = sum(hist_windows) / len(hist_windows)
        std_base = math.sqrt(sum((x - rate_base) ** 2 for x in hist_windows) / len(hist_windows))
        z_score = (c0 - rate_base) / (std_base + 0.5)

        author_diversity = min(1.0, max(0.25, unique_cnt / max(1, len(all_times))))
        cross_site_bonus = 1.0 + (sites_count - 1) * 0.5

        if c0 <= rate_base and acceleration <= 0:
            composite = 0.0
        else:
            raw_accel = max(0.0, z_score) * 1.8 + max(0.0, acceleration) * 4.0 + (sites_count - 1) * 0.8 + decayed_mass * 0.3
            composite = round(raw_accel * author_diversity * cross_site_bonus, 2)

        return {
            "velocity": round(velocity, 3),
            "acceleration_2nd": round(acceleration, 4),
            "z_score": round(z_score, 2),
            "unique_authors": unique_cnt,
            "composite_acceleration": composite
        }

import re
import math
import time
import hashlib
from collections import Counter
from datetime import datetime, timezone


class TrendMathEngine:
    """
    Чистое математическое ядро Проекта «Улей» (0 зашитых списков слов):
    1) Динамический корпусный IDF (_global_doc_freq): любые слова, встречающиеся более чем в 15% проверенных
       документов, автоматически признаются общим фоном языка и получают вес IDF = 0.0.
    2) Буфер накопления кандидатов (_candidate_sightings): одиночная публикация на 1 сайте не вызывает модель (0 токенов)
       и не создает карточку в БД. Сигнал рождается только при независимом появлении термина на >= 2 сайтах
       или у >= 2 разных авторов.
    3) Сравнение с Базой (calculate_kpis): если текущая частота упоминаний (c_now) не превышает среднюю базовую
       частоту прошлых отрезков (rate_base), итоговое ускорение строго равно 0.0.
    """
    _total_docs_seen: int = 0
    _global_doc_freq: Counter = Counter()
    _candidate_sightings: dict[str, list[dict]] = {}

    @classmethod
    def get_stem_idf(cls, stem: str) -> float:
        st = stem[:5]
        if len(st) < 4 or st.isdigit():
            return 0.0
        df = cls._global_doc_freq.get(st, 0)
        if cls._total_docs_seen >= 15 and (df / cls._total_docs_seen) > 0.15:
            return 0.0
        return math.log1p(max(2, cls._total_docs_seen) / (1.0 + df))

    @classmethod
    def is_noise_stem(cls, stem: str) -> bool:
        return cls.get_stem_idf(stem[:5]) <= 0.0

    @classmethod
    def compute_multilang_vector(cls, text: str) -> list[float]:
        vec = [0.0] * 1024
        t_low = text.lower()
        words = [w for w in re.findall(r"[a-zа-яё0-9\-]{4,}", t_low) if not w.isdigit()]
        stems = [w[:5] for w in words]

        for i, st in enumerate(stems):
            idf_w = cls.get_stem_idf(st)
            if idf_w <= 0.0:
                continue
            h = int(hashlib.md5(st.encode("utf-8")).hexdigest(), 16)
            vec[h % 640] += idf_w
            if i < len(stems) - 1:
                next_idf = cls.get_stem_idf(stems[i + 1])
                if next_idf > 0.0:
                    bg = f"{st}_{stems[i + 1]}"
                    h2 = int(hashlib.md5(bg.encode("utf-8")).hexdigest(), 16)
                    vec[640 + (h2 % 384)] += (idf_w + next_idf) * 0.9

        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 6) for x in vec]

    @classmethod
    def extract_clean_facts_and_terms(cls, raw_items: list[dict], source_name: str = "Source") -> dict:
        now_ts = time.time()
        if len(cls._candidate_sightings) > 600:
            cls._candidate_sightings = {
                k: [ev for ev in v if (now_ts - ev["ts"]) < 86400]
                for k, v in cls._candidate_sightings.items()
            }
            cls._candidate_sightings = {k: v for k, v in cls._candidate_sightings.items() if v}

        valid_items = [i for i in (raw_items or []) if len(i.get("text", "").strip()) > 45]
        if not valid_items:
            return {"has_signal": False}

        full_blob = " ".join([i["text"] for i in valid_items]).lower()
        doc_stems = {w[:5] for w in re.findall(r"\b[a-zа-яё]{5,}\b", full_blob) if not w.isdigit()}

        cls._total_docs_seen += 1
        if cls._total_docs_seen > 4000:
            cls._total_docs_seen = 1000
            for k in list(cls._global_doc_freq.keys()):
                cls._global_doc_freq[k] = max(1, cls._global_doc_freq[k] // 4)
        for st in doc_stems:
            cls._global_doc_freq[st] += 1

        stem_map = {}
        emerged_scores = Counter()

        for item in valid_items:
            txt = item["text"].strip()
            txt_low = txt.lower()
            item_words = {
                w for w in re.findall(r"\b[a-zA-Zа-яА-ЯёЁ0-9\-]{5,}\b", txt_low)
                if not w.isdigit()
            }
            for w in item_words:
                st = w[:5]
                idf_w = cls.get_stem_idf(st)
                if idf_w <= 0.0:
                    continue
                if st not in stem_map:
                    stem_map[st] = w

                sightings = cls._candidate_sightings.setdefault(st, [])
                if not any(s["source"] == source_name and s["text"][:60] == txt[:60] for s in sightings):
                    sightings.append({
                        "ts": now_ts,
                        "source": source_name,
                        "author": item.get("author_id", source_name),
                        "text": txt[:400]
                    })

                unique_sites = {s["source"] for s in sightings}
                unique_authors = {s["author"] for s in sightings}
                # Явление выходит из буфера только при независимом появлении на >= 2 сайтах ИЛИ у >= 2 авторов
                if len(unique_sites) >= 2 or (len(sightings) >= 2 and len(unique_authors) >= 2):
                    emerged_scores[st] = len(unique_sites) * idf_w + len(unique_authors) * 0.5

        if not emerged_scores:
            return {"has_signal": False}

        top_emerged = [st for st, _ in emerged_scores.most_common(4)]
        lead_stem = top_emerged[0]
        keywords = [stem_map[st] for st in top_emerged if st in stem_map]
        lead_sightings = cls._candidate_sightings.get(lead_stem, [])
        best_post = lead_sightings[-1]["text"] if lead_sightings else valid_items[0]["text"]

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", best_post) if len(s.strip()) > 25]
        clean_title = sentences[0] if sentences else best_post
        if len(clean_title) > 105:
            clean_title = clean_title[:105].rsplit(" ", 1)[0] + "..."

        clean_body = " ".join(sentences[:2]) if sentences else best_post
        if len(clean_body) > 350:
            clean_body = clean_body[:350].rsplit(" ", 1)[0] + "..."

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
        decay_lambda = math.log(2) / half_life_hours
        decayed_mass = 0.0

        authors_set = set()
        sources_all = set()
        timestamps = []

        for ev in events:
            ts_str = ev.get("timestamp")
            if ev.get("author"):
                authors_set.add(ev["author"])
            if ev.get("source"):
                sources_all.add(ev["source"])
            try:
                ev_time = datetime.fromisoformat(ts_str) if ts_str else now
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
            except Exception:
                ev_time = now

            timestamps.append(ev_time)
            age_hours = max(0.0, (now - ev_time).total_seconds() / 3600.0)
            decayed_mass += math.exp(-decay_lambda * age_hours)

        sites_count = len(sources_all)
        unique_cnt = max(len(authors_set), sites_count, 1)

        if len(events) < 2 or sites_count < 2:
            return {
                "velocity": 0.0,
                "acceleration_2nd": 0.0,
                "z_score": 0.0,
                "unique_authors": unique_cnt,
                "composite_acceleration": 0.0
            }

        timestamps.sort()
        t_first = timestamps[0]
        total_span_hours = max(1.0, (now - t_first).total_seconds() / 3600.0)

        seg_hours = max(0.5, total_span_hours / 3.0)
        c_base, c_mid, c_now = 0.0, 0.0, 0.0
        for ts in timestamps:
            rel_h = (ts - t_first).total_seconds() / 3600.0
            if rel_h < seg_hours:
                c_base += 1.0
            elif rel_h < seg_hours * 2.0:
                c_mid += 1.0
            else:
                c_now += 1.0

        rate_base = max(0.5, (c_base + c_mid) / 2.0)
        std_base = math.sqrt(((c_base - rate_base) ** 2 + (c_mid - rate_base) ** 2) / 2.0)

        velocity = (c_now - c_mid) / seg_hours
        acceleration = (c_now - 2.0 * c_mid + c_base) / (seg_hours ** 2)
        z_score = (c_now - rate_base) / (std_base + 0.5)

        author_diversity = min(1.0, max(0.25, unique_cnt / len(events)))
        cross_site_bonus = 1.0 + (sites_count - 1) * 0.5

        # Если текущая частота не превышает базовую и 2-я производная <= 0 — ускорения нет (0.0)
        if c_now <= rate_base and acceleration <= 0:
            composite = 0.0
        else:
            raw_accel = max(0.0, z_score) * 1.8 + max(0.0, acceleration) * 4.0 + (sites_count - 1) * 1.0 + decayed_mass * 0.3
            composite = round(raw_accel * author_diversity * cross_site_bonus, 2)

        return {
            "velocity": round(velocity, 3),
            "acceleration_2nd": round(acceleration, 4),
            "z_score": round(z_score, 2),
            "unique_authors": unique_cnt,
            "composite_acceleration": composite
        }

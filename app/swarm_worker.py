import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.models import (
    Resource, SemanticBlock, SwarmBlackboard, 
    TrendMemory, AgentTelemetry, MetaInsight
)
from app.connectors import ResourceConnectorEngine
from app.llm_router import DynamicModelRouter
from app.math_engine import TrendMathEngine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_multi_site_note(delta_history: list[dict]) -> str:
    now = utcnow()
    mentions_24h = 0
    sites_24h = []

    for ev in (delta_history or []):
        ts_str = ev.get("timestamp")
        try:
            ev_time = datetime.fromisoformat(ts_str) if ts_str else now
            if ev_time.tzinfo is None:
                ev_time = ev_time.replace(tzinfo=timezone.utc)
        except Exception:
            ev_time = now

        if (now - ev_time).total_seconds() / 3600.0 <= 24.0:
            mentions_24h += 1
            src = ev.get("source") or ev.get("geo") or ""
            if src and src not in sites_24h:
                sites_24h.append(src)

    if len(sites_24h) >= 2 or mentions_24h >= 3:
        return f"\n(Зафиксировано {mentions_24h} раз на {len(sites_24h)} ресурсах за 24 ч: {', '.join(sites_24h[:5])})"
    return ""


async def update_agent_telemetry(
    db: AsyncSession,
    agent_id: str,
    agent_type: str,
    state: str,
    resource_name: str,
    action: str,
    inc_items: int = 0
):
    tel = (await db.execute(select(AgentTelemetry).where(AgentTelemetry.agent_id == agent_id))).scalars().first()
    if not tel:
        tel = AgentTelemetry(agent_id=agent_id, agent_type=agent_type, items_processed=0)
        db.add(tel)
    tel.agent_type = agent_type
    tel.state = state
    tel.current_resource = resource_name
    tel.current_action = action
    tel.items_processed = (tel.items_processed or 0) + inc_items
    tel.updated_at = utcnow()
    await db.commit()


class ScoutAndForagerBee:
    def __init__(self, agent_id: str, controller: "SwarmController"):
        self.agent_id = agent_id
        self.controller = controller

    async def run_forever(self):
        while self.controller.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    handled = await self.forage_by_waggle_dance(db)
                    if not handled and self.controller.is_running:
                        await self.scout_flowers(db)
            except asyncio.CancelledError:
                break
            except Exception as e:
                async with AsyncSessionLocal() as db:
                    await update_agent_telemetry(
                        db, self.agent_id, "РАЗВЕДЧИЦА / ФУРАЖИР", "ERROR", "-", f"Помеха: {str(e)[:90]}"
                    )
            await asyncio.sleep(8)

        async with AsyncSessionLocal() as db:
            await update_agent_telemetry(db, self.agent_id, "РАЗВЕДЧИЦА / ФУРАЖИР", "STOPPED", "-", "В улье (На паузе)")

    async def forage_by_waggle_dance(self, db: AsyncSession) -> bool:
        """
        Атомарно захватывает задание на Доске, СНАЧАЛА фиксирует шаг проверки в БД (чтобы не потерять лок),
        и проверяет ключи сразу по всем закешированным сайтам целевого блока за 0 байт трафика.
        """
        stmt = (
            select(SwarmBlackboard)
            .where(SwarmBlackboard.status == "ACTIVE")
            .order_by(SwarmBlackboard.priority_boost.desc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        signal = (await db.execute(stmt)).scalars().first()
        if not signal:
            return False

        unchecked = [b for b in (signal.target_block_types or []) if b not in (signal.checked_by_blocks or [])]
        if not unchecked or not signal.search_queries:
            signal.status = "PROMOTED_TO_MEMORY"
            await db.commit()
            return True

        target_block = unchecked[0]
        # Сначала фиксируем захват блока в рамках текущей транзакции FOR UPDATE!
        checked = list(signal.checked_by_blocks or [])
        checked.append(target_block)
        signal.checked_by_blocks = checked
        if len(checked) >= len(signal.target_block_types or []):
            signal.status = "PROMOTED_TO_MEMORY"
        await db.commit()

        block_resources = (
            await db.execute(
                select(Resource).join(SemanticBlock).where(
                    SemanticBlock.block_type == target_block,
                    Resource.is_active == True
                )
            )
        ).scalars().all()

        await update_agent_telemetry(
            db, self.agent_id, "РАБОЧАЯ ПЧЕЛА (ФУРАЖИР)", "FORAGING", f"Блок [{target_block}] ({len(block_resources)} сайтов)",
            f"Сверка ключей {signal.search_queries[:3]} сразу по всем сайтам блока [{target_block}]"
        )

        hits = ResourceConnectorEngine.search_block_cache(block_resources, signal.search_queries)
        for matched_res, matched_items in hits[:3]:
            sample_text = " . ".join([i["text"][:200] for i in matched_items[:2]])
            authors = list({i.get("author_id", matched_res.name) for i in matched_items[:2]})
            await self.hand_over_to_receiver_bee(
                db, signal, matched_res.geo_region,
                source_override=matched_res.name,
                event_override=sample_text[:240],
                authors=authors,
                is_mainstream=(target_block == "mainstream_media")
            )
        return True

    async def scout_flowers(self, db: AsyncSession):
        now = utcnow()
        stmt = (
            select(Resource, SemanticBlock.block_type)
            .join(SemanticBlock)
            .where(
                Resource.is_active == True,
                Resource.locked_by_agent == None,
                or_(Resource.cooldown_until == None, Resource.cooldown_until < now)
            )
            .order_by(Resource.last_crawled_at.asc().nullsfirst(), Resource.temperature.desc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if not row:
            await update_agent_telemetry(
                db, self.agent_id, "ПЧЕЛА-РАЗВЕДЧИЦА", "IDLE", "В улье",
                "Все 120 ресурсов проверены, режим экономии трафика и токенов..."
            )
            await asyncio.sleep(15)
            return

        resource, block_type = row[0], row[1]
        resource.locked_by_agent = self.agent_id
        resource.locked_at = now
        await db.commit()

        try:
            tags_str = ", ".join(resource.tags or [])
            await update_agent_telemetry(
                db, self.agent_id, "ПЧЕЛА-РАЗВЕДЧИЦА", "SCOUTING", resource.name,
                f"Осмотр [{resource.connector_type}] ({resource.geo_region}) {tags_str}"
            )
            items = await ResourceConnectorEngine.fetch(db, resource)
            resource.last_crawled_at = utcnow()
            resource.fail_count = 0
            resource.health_status = "HEALTHY"
            resource.temperature = max(25.0, round(resource.temperature - 25.0, 1))

            if not items:
                resource.cooldown_until = utcnow() + timedelta(minutes=45)
                await update_agent_telemetry(
                    db, self.agent_id, "ПЧЕЛА-РАЗВЕДЧИЦА", "IDLE", resource.name,
                    "304 Not Modified / Новых записей нет (Пауза 45 мин / 0 токенов)"
                )
                return

            if block_type == "mainstream_media":
                media_blob = " ".join([i["text"].lower() for i in items])
                active_trends = (
                    await db.execute(select(TrendMemory).where(TrendMemory.is_in_mainstream_media == False))
                ).scalars().all()
                flagged = 0
                for tr in active_trends:
                    stems = [k.lower()[:5] for k in (tr.keywords_multilang or []) if len(k) >= 4]
                    if len(stems) >= 2 and sum(1 for s in stems if s in media_blob) >= 2:
                        tr.is_in_mainstream_media = True
                        tr.lifecycle_status = "MAINSTREAM_PRICED_IN"
                        flagged += 1
                resource.cooldown_until = utcnow() + timedelta(minutes=45)
                await update_agent_telemetry(
                    db, self.agent_id, "ПЧЕЛА-РАЗВЕДЧИЦА", "CALIBRATING", resource.name,
                    f"Калибровка по СМИ (0 токенов): отсечено вышедших в тираж трендов: {flagged}",
                    inc_items=len(items)
                )
                return

            combined_text = " \n ".join([i["text"] for i in items[:6]])
            authors = list({i.get("author_id", resource.name) for i in items[:6]})

            content_hash = hashlib.md5(combined_text[:1500].encode("utf-8")).hexdigest()
            if resource.last_content_hash == content_hash:
                resource.cooldown_until = utcnow() + timedelta(minutes=45)
                await update_agent_telemetry(
                    db, self.agent_id, "ПЧЕЛА-РАЗВЕДЧИЦА", "IDLE", resource.name,
                    "Контент без изменений — пропуск LLM (Пауза 45 мин / 0 токенов)"
                )
                return
            resource.last_content_hash = content_hash

            await update_agent_telemetry(
                db, self.agent_id, "ПЧЕЛА-РАЗВЕДЧИЦА", "ANALYZING", resource.name,
                f"Проверка {len(items)} записей через матрицу зарождения трендов...", inc_items=len(items)
            )
            analysis = await DynamicModelRouter.ask_agent(
                db, "dirty_scout", combined_text[:650], raw_items=items[:6], source_name=resource.name
            )

            if analysis.get("is_anomaly", False) and analysis.get("multilang_keywords"):
                resource.temperature = min(90.0, round(resource.temperature + 30.0, 1))
                resource.cooldown_until = utcnow() + timedelta(minutes=25)
                semantic_core = " ".join(analysis.get("multilang_keywords", [])) * 2 + " " + analysis.get("title", "")
                vec = DynamicModelRouter.compute_fast_vector(semantic_core)

                new_sig = SwarmBlackboard(
                    source_resource_name=resource.name,
                    signal_summary=analysis.get("essence", ""),
                    search_queries=analysis.get("multilang_keywords", []),
                    embedding=vec,
                    target_block_types=["investments", "b2b_patents", "mainstream_media"]
                )
                db.add(new_sig)
                await db.flush()
                await self.hand_over_to_receiver_bee(
                    db, new_sig, resource.geo_region,
                    analysis=analysis, authors=authors, is_mainstream=False
                )
            else:
                resource.cooldown_until = utcnow() + timedelta(minutes=40)
        except Exception as err:
            resource.fail_count = (resource.fail_count or 0) + 1
            resource.last_error = str(err)[:200]
            resource.health_status = "DEGRADED"
            resource.cooldown_until = utcnow() + timedelta(minutes=25)
        finally:
            resource.locked_by_agent = None
            await db.commit()

    async def hand_over_to_receiver_bee(
        self,
        db: AsyncSession,
        signal: SwarmBlackboard,
        geo: str,
        analysis: dict = None,
        source_override: str = None,
        event_override: str = None,
        authors: list[str] = None,
        is_mainstream: bool = False
    ):
        dist_col = TrendMemory.embedding.cosine_distance(signal.embedding).label("dist")
        stmt = select(TrendMemory, dist_col).order_by(dist_col).limit(1)
        row = (await db.execute(stmt)).first()
        existing = row[0] if (row and row[1] is not None and row[1] < 0.35) else None

        if not existing and signal.search_queries:
            sig_stems = {k.lower()[:5] for k in signal.search_queries if len(k) >= 4}
            recent_trends = (await db.execute(select(TrendMemory).order_by(TrendMemory.id.desc()).limit(80))).scalars().all()
            for tr in recent_trends:
                tr_stems = {k.lower()[:5] for k in (tr.keywords_multilang or []) if len(k) >= 4}
                if len(sig_stems.intersection(tr_stems)) >= 2:
                    existing = tr
                    break

        now_iso = utcnow().isoformat()
        src_name = source_override or signal.source_resource_name
        ev_text = event_override or signal.signal_summary
        primary_author = authors[0] if authors else src_name
        clean_title = (analysis or {}).get("title") or signal.signal_summary[:90]
        raw_essence = (analysis or {}).get("core_essence") or signal.signal_summary

        initial_deltas = []
        if not existing and signal.search_queries:
            lead_st = signal.search_queries[0].lower()[:5]
            prior_sightings = TrendMathEngine._candidate_sightings.get(lead_st, [])
            for ps in prior_sightings[-4:]:
                if ps["source"] != src_name:
                    initial_deltas.append({
                        "timestamp": datetime.fromtimestamp(ps["ts"], tz=timezone.utc).isoformat(),
                        "date": datetime.fromtimestamp(ps["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "source": ps["source"],
                        "author": ps["author"],
                        "geo": geo,
                        "event": ps["text"][:240]
                    })

        new_event = {
            "timestamp": now_iso,
            "date": utcnow().strftime("%Y-%m-%d %H:%M"),
            "source": src_name,
            "author": primary_author,
            "geo": geo,
            "event": ev_text
        }

        if not existing:
            count_all = len((await db.execute(select(TrendMemory.id))).scalars().all())
            all_deltas = initial_deltas + [new_event]
            note = get_multi_site_note(all_deltas)
            db.add(TrendMemory(
                code=f"HIVE-{count_all + 1:04d}",
                title=clean_title,
                core_essence=f"{raw_essence}{note}",
                keywords_multilang=signal.search_queries,
                embedding=signal.embedding,
                origin_geo=geo,
                geo_spread=[geo],
                lifecycle_status="MAINSTREAM_PRICED_IN" if is_mainstream else ("ACCELERATING" if len(all_deltas) >= 2 else "CALIBRATING_BASELINE"),
                is_in_mainstream_media=is_mainstream,
                acceleration_rate=0.0,
                velocity=0.0,
                z_score=0.0,
                delta_history=all_deltas
            ))
        else:
            if existing.lifecycle_status == "IGNORED_NOISE":
                return
            hist = list(existing.delta_history or [])
            if any(d.get("event") == ev_text and d.get("source") == src_name for d in hist):
                return
            hist.append(new_event)
            existing.delta_history = hist

            note = get_multi_site_note(hist)
            base_body = (existing.core_essence or raw_essence).split("\n(Зафиксировано")[0]
            existing.core_essence = f"{base_body}{note}"

            spread = set(existing.geo_spread or [])
            spread.add(geo)
            existing.geo_spread = list(spread)
            kw = set(existing.keywords_multilang or [])
            kw.update(signal.search_queries or [])
            existing.keywords_multilang = list(kw)[:10]
            if is_mainstream:
                existing.is_in_mainstream_media = True
                existing.lifecycle_status = "MAINSTREAM_PRICED_IN"
        await db.commit()


class QueenBeeAgent:
    def __init__(self, controller: "SwarmController"):
        self.agent_id = "Пчелиная Королева"
        self.controller = controller

    async def run_forever(self):
        while self.controller.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    all_res = (await db.execute(select(Resource).where(Resource.is_active == True))).scalars().all()
                    warmed_up = 0
                    for r in all_res:
                        if r.temperature < 55.0:
                            r.temperature = round(r.temperature + 12.0, 1)
                            warmed_up += 1
                    active_sigs = (await db.execute(select(SwarmBlackboard).where(SwarmBlackboard.status == "ACTIVE"))).scalars().all()
                    for sig in active_sigs:
                        sig.priority_boost = round((sig.priority_boost or 1.0) + 0.5, 1)

                    await update_agent_telemetry(
                        db, self.agent_id, "КОРОЛЕВА", "ORCHESTRATING",
                        f"Поле из {len(all_res)} URL (Пчёл: {self.controller.scout_count})",
                        f"Подогрето нижних холодных ресурсов: {warmed_up} шт. | Активных танцев на Доске: {len(active_sigs)}",
                        inc_items=1
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ QueenBee error: {e}")
            await asyncio.sleep(45)


class GuardBeeAgent:
    def __init__(self, controller: "SwarmController"):
        self.agent_id = "Пчела-Страж"
        self.controller = controller

    async def run_forever(self):
        while self.controller.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    now = utcnow()
                    locks, healed = 0, 0
                    for r in (await db.execute(select(Resource))).scalars().all():
                        if r.locked_by_agent and r.locked_at and (now - r.locked_at).total_seconds() > 90:
                            r.locked_by_agent, r.locked_at = None, None
                            locks += 1
                        if r.health_status == "DEGRADED" or (r.fail_count and r.fail_count >= 3):
                            r.health_status, r.fail_count = "REANIMATING", 0
                            r.cooldown_until = now + timedelta(minutes=30)
                            healed += 1

                    await db.execute(
                        delete(SwarmBlackboard).where(
                            SwarmBlackboard.status == "PROMOTED_TO_MEMORY",
                            SwarmBlackboard.created_at < (now - timedelta(hours=4))
                        )
                    )
                    revived = await self.controller.ensure_scouts_alive()
                    await update_agent_telemetry(
                        db, self.agent_id, "СТРАЖ", "GUARDING", "Леток Улья & Сеть",
                        f"Снято зависших локов: {locks} | Восстановлено URL: {healed} | В строю разведчиц: {self.controller.scout_count}",
                        inc_items=locks + healed + revived
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ GuardBee error: {e}")
            await asyncio.sleep(50)


class ReceiverMathBeeAgent:
    def __init__(self, controller: "SwarmController"):
        self.agent_id = "Пчела-Приёмщица (Математика)"
        self.controller = controller

    async def run_forever(self):
        while self.controller.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    trends = (await db.execute(select(TrendMemory).where(TrendMemory.lifecycle_status != "IGNORED_NOISE"))).scalars().all()
                    if not trends:
                        await update_agent_telemetry(
                            db, self.agent_id, "ПРИЁМЩИЦА", "IDLE", "Соты Памяти (0)",
                            "Ожидание подтвержденных межсайтовых сигналов..."
                        )
                        await asyncio.sleep(25)
                        continue

                    accelerating_cnt = 0
                    for tr in trends:
                        deltas = tr.delta_history or []
                        kpis = TrendMathEngine.calculate_kpis(deltas)
                        tr.velocity = kpis.get("velocity", 0.0)
                        tr.z_score = kpis.get("z_score", 0.0)
                        tr.unique_authors_count = kpis.get("unique_authors", 1)
                        tr.acceleration_rate = kpis.get("composite_acceleration", 0.0)
                        tr.last_analyzed_at = utcnow()

                        note = get_multi_site_note(deltas)
                        base_body = (tr.core_essence or tr.title).split("\n(Зафиксировано")[0]
                        tr.core_essence = f"{base_body}{note}"

                        if tr.is_in_mainstream_media:
                            tr.lifecycle_status = "MAINSTREAM_PRICED_IN"
                        elif tr.acceleration_rate > 0:
                            tr.lifecycle_status = "ACCELERATING"
                            accelerating_cnt += 1
                        else:
                            tr.lifecycle_status = "CALIBRATING_BASELINE"
                    await db.commit()

                    existing_metas = (await db.execute(select(MetaInsight))).scalars().all()
                    already_paired = {tuple(sorted(m.linked_trend_codes or [])) for m in existing_metas}

                    new_pairs_found = 0
                    recent_trends = sorted(trends, key=lambda x: (x.acceleration_rate, x.id), reverse=True)[:60]
                    for i in range(len(recent_trends)):
                        t1 = recent_trends[i]
                        kw1 = {k for k in (t1.keywords_multilang or []) if not TrendMathEngine.is_noise_stem(k[:5])}
                        for j in range(i + 1, len(recent_trends)):
                            t2 = recent_trends[j]
                            pair_key = tuple(sorted([t1.code, t2.code]))
                            if pair_key in already_paired:
                                continue
                            kw2 = {k for k in (t2.keywords_multilang or []) if not TrendMathEngine.is_noise_stem(k[:5])}
                            common_kw = kw1.intersection(kw2)
                            src1 = t1.delta_history[0].get("source", t1.origin_geo) if t1.delta_history else t1.origin_geo
                            src2 = t2.delta_history[0].get("source", t2.origin_geo) if t2.delta_history else t2.origin_geo

                            if common_kw and src1 != src2:
                                already_paired.add(pair_key)
                                shared_str = ", ".join(list(common_kw)[:3])
                                pair_prompt = f"Тренд 1 ({src1}): {t1.title}. Тренд 2 ({src2}): {t2.title}. Общий маркер: {shared_str}"
                                llm_meta = await DynamicModelRouter.ask_agent(db, "memory_analyst", pair_prompt)

                                db.add(MetaInsight(
                                    title=llm_meta.get("title") or f"Межсайтовая зависимость по объекту [{shared_str}] ({t1.code} + {t2.code})",
                                    linked_trend_codes=list(pair_key),
                                    pattern_type="CROSS_CHANNEL_RAG",
                                    consumer_pain_summary=llm_meta.get("consumer_pain_summary") or f"1) [{src1}]: {t1.title}\n2) [{src2}]: {t2.title}",
                                    investment_or_product_thesis=llm_meta.get("investment_or_product_thesis") or (
                                        f"Специфический маркер [{shared_str}] независимо проявился в разных источниках ({src1} и {src2}). "
                                        f"Доступен для глубокого опроса через RAG-шлюз Гермеса: /api/hermes/search?q={list(common_kw)[0]}"
                                    ),
                                    confidence_score=float(llm_meta.get("confidence_score", 0.86))
                                ))
                                new_pairs_found += 1
                                if new_pairs_found >= 2:
                                    break
                        if new_pairs_found >= 2:
                            break
                    if new_pairs_found > 0:
                        await db.commit()

                    await update_agent_telemetry(
                        db, self.agent_id, "ПРИЁМЩИЦА", "IDLE", f"Соты Памяти ({len(trends)} трендов)",
                        f"Ускоряются к базе: {accelerating_cnt} | На замере базы: {len(trends) - accelerating_cnt} | Новых межсайтовых связок: +{new_pairs_found}",
                        inc_items=new_pairs_found
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ ReceiverMathBee error: {e}")
            await asyncio.sleep(30)


class SwarmController:
    def __init__(self):
        self.is_running = False
        self.scout_tasks: dict[str, asyncio.Task] = {}
        self.special_tasks: list[asyncio.Task] = []
        self.scout_count = 4

    async def start(self, scout_count: int = None):
        if scout_count is not None:
            self.scout_count = max(1, min(100, scout_count))
        if self.is_running:
            await self.scale_scouts(self.scout_count)
            return
        self.is_running = True
        async with AsyncSessionLocal() as db:
            for r in (await db.execute(select(Resource))).scalars().all():
                r.locked_by_agent = None
            # Прогреваем матрицу IDF из уже накопленной базы трендов при старте
            existing_titles = (await db.execute(select(TrendMemory.core_essence))).scalars().all()
            TrendMathEngine.warmup_from_db(list(existing_titles))
            await db.commit()

        for i in range(1, self.scout_count + 1):
            aid = f"Пчела-Разведчица #{i}"
            self.scout_tasks[aid] = asyncio.create_task(ScoutAndForagerBee(aid, self).run_forever())
        self.special_tasks = [
            asyncio.create_task(QueenBeeAgent(self).run_forever()),
            asyncio.create_task(GuardBeeAgent(self).run_forever()),
            asyncio.create_task(ReceiverMathBeeAgent(self).run_forever()),
        ]

    async def scale_scouts(self, new_count: int):
        new_count = max(1, min(100, new_count))
        old_count = self.scout_count
        self.scout_count = new_count
        if not self.is_running:
            return
        if new_count > old_count:
            for i in range(old_count + 1, new_count + 1):
                aid = f"Пчела-Разведчица #{i}"
                if aid not in self.scout_tasks or self.scout_tasks[aid].done():
                    self.scout_tasks[aid] = asyncio.create_task(ScoutAndForagerBee(aid, self).run_forever())
        elif new_count < old_count:
            async with AsyncSessionLocal() as db:
                for i in range(new_count + 1, old_count + 1):
                    aid = f"Пчела-Разведчица #{i}"
                    task = self.scout_tasks.pop(aid, None)
                    if task:
                        task.cancel()
                    tel = (await db.execute(select(AgentTelemetry).where(AgentTelemetry.agent_id == aid))).scalars().first()
                    if tel:
                        await db.delete(tel)
                await db.commit()

    async def ensure_scouts_alive(self) -> int:
        revived = 0
        if not self.is_running:
            return 0
        for i in range(1, self.scout_count + 1):
            aid = f"Пчела-Разведчица #{i}"
            t = self.scout_tasks.get(aid)
            if not t or t.done():
                self.scout_tasks[aid] = asyncio.create_task(ScoutAndForagerBee(aid, self).run_forever())
                revived += 1
        return revived

    async def stop(self):
        self.is_running = False
        for t in list(self.scout_tasks.values()) + self.special_tasks:
            t.cancel()
        self.scout_tasks.clear()
        self.special_tasks.clear()
        async with AsyncSessionLocal() as db:
            for tel in (await db.execute(select(AgentTelemetry))).scalars().all():
                tel.state = "STOPPED"
                tel.current_action = "В улье (Остановлена)"
            await db.commit()


swarm_controller = SwarmController()

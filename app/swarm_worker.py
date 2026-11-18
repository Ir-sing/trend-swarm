import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.models import Resource, SemanticBlock, SwarmBlackboard, TrendMemory, AgentTelemetry, MetaInsight
from app.connectors import ResourceConnectorEngine
from app.llm_router import DynamicModelRouter
from app.math_engine import TrendMathEngine

def utcnow():
    return datetime.now(timezone.utc)

async def update_agent_telemetry(db: AsyncSession, agent_id: str, agent_type: str, state: str, resource_name: str, action: str, inc_items: int = 0):
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

class AutonomousScout:
    def __init__(self, agent_id: str, controller: "SwarmController"):
        self.agent_id = agent_id
        self.controller = controller

    async def run_forever(self):
        while self.controller.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    handled = await self.check_blackboard_signals(db)
                    if not handled and self.controller.is_running:
                        await self.patrol_resources(db)
            except asyncio.CancelledError:
                break
            except Exception as e:
                async with AsyncSessionLocal() as db:
                    await update_agent_telemetry(db, self.agent_id, "SCOUT", "ERROR", "-", f"Сбой: {str(e)[:100]}")
            await asyncio.sleep(4)

        async with AsyncSessionLocal() as db:
            await update_agent_telemetry(db, self.agent_id, "SCOUT", "STOPPED", "-", "Остановлен")

    async def check_blackboard_signals(self, db: AsyncSession) -> bool:
        stmt = select(SwarmBlackboard).where(SwarmBlackboard.status == "ACTIVE").order_by(SwarmBlackboard.priority_boost.desc())
        signals = (await db.execute(stmt)).scalars().all()
        for signal in signals:
            unchecked = [b for b in signal.target_block_types if b not in signal.checked_by_blocks]
            if not unchecked:
                signal.status = "PROMOTED_TO_MEMORY"
                await db.commit()
                continue
            target_block = unchecked[0]
            res_stmt = (
                select(Resource).join(SemanticBlock)
                .where(SemanticBlock.block_type == target_block, Resource.is_active == True, Resource.locked_by_agent == None)
                .order_by(Resource.temperature.desc())
            )
            resource = (await db.execute(res_stmt)).scalars().first()
            if not resource:
                continue

            resource.locked_by_agent = self.agent_id
            resource.locked_at = utcnow()
            await db.commit()

            try:
                q = signal.search_queries[0] if signal.search_queries else ""
                await update_agent_telemetry(db, self.agent_id, "SCOUT", "CROSS_CHECKING", resource.name, f"Проверка сигнала '{signal.signal_summary[:40]}...' в [{target_block}]")
                items = await ResourceConnectorEngine.fetch(db, resource, search_query=q)
                checked = list(signal.checked_by_blocks)
                checked.append(target_block)
                signal.checked_by_blocks = checked
                if items:
                    sample_text = " | ".join([i["text"][:200] for i in items[:4]])
                    authors = list({i.get("author_id", resource.name) for i in items[:4]})
                    analysis = await DynamicModelRouter.ask_agent(db, "dirty_scout", f"Сигнал: {signal.signal_summary}. Найдено в {resource.name}: {sample_text}")
                    if analysis.get("confirmed", False):
                        conf = list(signal.confirmations)
                        conf.append({"block": target_block, "resource": resource.name, "geo": resource.geo_region, "details": analysis.get("summary", sample_text[:150])})
                        signal.confirmations = conf
                        await self.record_to_memory(db, signal, resource.geo_region, authors=authors, is_mainstream=(target_block == "mainstream_media"))
            except Exception as err:
                resource.fail_count = (resource.fail_count or 0) + 1
                resource.last_error = str(err)[:200]
                resource.health_status = "DEGRADED"
            finally:
                resource.locked_by_agent = None
                await db.commit()
            return True
        return False

    async def patrol_resources(self, db: AsyncSession):
        now = utcnow()
        stmt = (
            select(Resource)
            .where(Resource.is_active == True, Resource.locked_by_agent == None, or_(Resource.cooldown_until == None, Resource.cooldown_until < now))
            .order_by(Resource.temperature.desc(), Resource.last_crawled_at.asc().nullsfirst())
        )
        resource = (await db.execute(stmt)).scalars().first()
        if not resource:
            await update_agent_telemetry(db, self.agent_id, "SCOUT", "IDLE", "-", "Все активные URL проверены (Режим экономии трафика)...")
            await asyncio.sleep(6)
            return

        resource.locked_by_agent = self.agent_id
        resource.locked_at = now
        await db.commit()

        try:
            tags_str = ", ".join(resource.tags or [])
            await update_agent_telemetry(db, self.agent_id, "SCOUT", "CRAWLING", resource.name, f"Запрос [{resource.connector_type}] ({resource.geo_region}) {tags_str}")
            items = await ResourceConnectorEngine.fetch(db, resource)
            resource.last_crawled_at = utcnow()
            resource.fail_count = 0
            resource.health_status = "HEALTHY"

            if not items:
                resource.temperature = max(15.0, round(resource.temperature * 0.95, 1))
                resource.cooldown_until = utcnow() + timedelta(minutes=15)
                await update_agent_telemetry(db, self.agent_id, "SCOUT", "IDLE", resource.name, "304 Not Modified / Новых данных нет (0 байт / 0 токенов)")
                return

            combined_text = " \n ".join([f"[{i['author_id']}]: {i['text']}" for i in items[:8]])
            authors = list({i.get("author_id", resource.name) for i in items[:8]})
            content_hash = hashlib.md5(combined_text[:1500].encode("utf-8")).hexdigest()
            if resource.last_content_hash == content_hash:
                resource.cooldown_until = utcnow() + timedelta(minutes=20)
                await update_agent_telemetry(db, self.agent_id, "SCOUT", "IDLE", resource.name, "Контент не изменился — пропуск LLM (0 токенов)")
                return
            resource.last_content_hash = content_hash

            first_img = next((i["image_url"] for i in items if i.get("image_url")), None)
            role = "vision_meme" if first_img else "dirty_scout"

            await update_agent_telemetry(db, self.agent_id, "SCOUT", "ANALYZING", resource.name, f"Анализ {len(items)} свежих записей ролью [{role}]...", inc_items=len(items))
            analysis = await DynamicModelRouter.ask_agent(db, role, f"Источник: {resource.name} ({resource.geo_region}). Данные:\n{combined_text[:2500]}", image_url=first_img)

            if analysis.get("is_anomaly", False):
                resource.temperature = min(100.0, round(resource.temperature + 15.0, 1))
                resource.cooldown_until = utcnow() + timedelta(minutes=10)
                vec = DynamicModelRouter.compute_fast_vector(analysis.get("essence", combined_text[:300]))
                new_sig = SwarmBlackboard(
                    source_resource_name=resource.name,
                    signal_summary=analysis.get("essence", "Обнаружен всплеск"),
                    search_queries=analysis.get("multilang_keywords", ["тренд"]),
                    embedding=vec,
                    target_block_types=["investments", "b2b_patents", "mainstream_media"]
                )
                db.add(new_sig)
                await db.flush()
                await self.record_to_memory(db, new_sig, resource.geo_region, authors=authors, is_mainstream=False)
            else:
                resource.temperature = max(15.0, round(resource.temperature * 0.95, 1))
                resource.cooldown_until = utcnow() + timedelta(minutes=20)
        except Exception as err:
            resource.fail_count = (resource.fail_count or 0) + 1
            resource.last_error = str(err)[:200]
            resource.health_status = "DEGRADED"
            resource.cooldown_until = utcnow() + timedelta(minutes=10)
        finally:
            resource.locked_by_agent = None
            await db.commit()

    async def record_to_memory(self, db: AsyncSession, signal: SwarmBlackboard, geo: str, authors: list[str] = None, is_mainstream: bool = False):
        # Честный поиск смыслового дубля по косинусному расстоянию в pgvector (порог < 0.38)
        dist_col = TrendMemory.embedding.cosine_distance(signal.embedding).label("dist")
        stmt = select(TrendMemory, dist_col).order_by(dist_col).limit(1)
        row = (await db.execute(stmt)).first()
        existing = row[0] if (row and row[1] is not None and row[1] < 0.38) else None

        synthesis = await DynamicModelRouter.ask_agent(db, "synthesizer", f"Сигнал из {signal.source_resource_name} ({geo}): {signal.signal_summary}. Подтверждения: {signal.confirmations}")
        now_iso = utcnow().isoformat()
        primary_author = authors[0] if authors else signal.source_resource_name

        if not existing:
            count_all = len((await db.execute(select(TrendMemory.id))).scalars().all())
            db.add(TrendMemory(
                code=f"TREND-{count_all + 1:04d}",
                title=synthesis.get("title", signal.signal_summary[:70]),
                core_essence=synthesis.get("core_essence", signal.signal_summary),
                keywords_multilang=signal.search_queries,
                embedding=signal.embedding,
                origin_geo=geo,
                geo_spread=[geo],
                lifecycle_status="MAINSTREAM_PRICED_IN" if is_mainstream else "NEW_SIGNAL",
                is_in_mainstream_media=is_mainstream,
                acceleration_rate=1.0,
                delta_history=[{
                    "timestamp": now_iso,
                    "date": utcnow().strftime("%Y-%m-%d %H:%M"),
                    "source": signal.source_resource_name,
                    "author": primary_author,
                    "geo": geo,
                    "event": signal.signal_summary
                }]
            ))
        else:
            if existing.lifecycle_status == "IGNORED_NOISE":
                return
            hist = list(existing.delta_history or [])
            hist.append({
                "timestamp": now_iso,
                "date": utcnow().strftime("%Y-%m-%d %H:%M"),
                "source": signal.source_resource_name,
                "author": primary_author,
                "geo": geo,
                "event": synthesis.get("delta_summary", signal.signal_summary)
            })
            existing.delta_history = hist
            spread = set(existing.geo_spread or [])
            spread.add(geo)
            existing.geo_spread = list(spread)
            kw = set(existing.keywords_multilang or [])
            kw.update(signal.search_queries or [])
            existing.keywords_multilang = list(kw)[:12]
            if is_mainstream:
                existing.is_in_mainstream_media = True
                existing.lifecycle_status = "MAINSTREAM_PRICED_IN"
        await db.commit()

class ForemanAgent:
    def __init__(self, controller: "SwarmController"):
        self.agent_id = "Foreman-Прораб"
        self.controller = controller

    async def run_forever(self):
        while self.controller.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    blocks = (await db.execute(select(SemanticBlock))).scalars().all()
                    starving_name, low_t = "-", 100.0
                    for b in blocks:
                        r_list = (await db.execute(select(Resource).where(Resource.block_id == b.id, Resource.is_active == True))).scalars().all()
                        if not r_list:
                            continue
                        avg_t = sum(r.temperature for r in r_list) / len(r_list)
                        if avg_t < low_t:
                            low_t, starving_name = avg_t, b.name
                        if avg_t < 45.0:
                            for r in r_list:
                                r.temperature = min(90.0, round(r.temperature + 15.0, 1))
                    active_sigs = (await db.execute(select(SwarmBlackboard).where(SwarmBlackboard.status == "ACTIVE"))).scalars().all()
                    for sig in active_sigs:
                        sig.priority_boost = round((sig.priority_boost or 1.0) + 0.5, 1)
                    await update_agent_telemetry(db, self.agent_id, "FOREMAN", "ORCHESTRATING", f"Контроль 6 блоков (Ищеек: {self.controller.scout_count})", f"Самый холодный блок: '{starving_name}' ({low_t:.1f}°). Нарядов на Доске: {len(active_sigs)}", inc_items=1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Foreman error: {e}")
            await asyncio.sleep(20)

class ReanimatorAgent:
    def __init__(self, controller: "SwarmController"):
        self.agent_id = "Reanimator-Реаниматор"
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
                            r.cooldown_until = now + timedelta(minutes=15)
                            healed += 1
                    # Очистка отработанных записей Доски старше 12 часов, чтобы БД не пухла
                    await db.execute(delete(SwarmBlackboard).where(SwarmBlackboard.status == "PROMOTED_TO_MEMORY", SwarmBlackboard.created_at < (now - timedelta(hours=12))))
                    revived = await self.controller.ensure_scouts_alive()
                    await update_agent_telemetry(db, self.agent_id, "REANIMATOR", "HEALING", "Инфраструктура & БД", f"Снято зомби-локов: {locks} | Восстановлено URL: {healed} | Поднято ищеек: {revived}", inc_items=locks+healed+revived)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Reanimator error: {e}")
            await asyncio.sleep(20)

class MemoryAnalystAgent:
    def __init__(self, controller: "SwarmController"):
        self.agent_id = "Analyst-Память"
        self.controller = controller
        self.last_state_hash = None

    async def run_forever(self):
        while self.controller.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    trends = (await db.execute(select(TrendMemory).where(TrendMemory.lifecycle_status != "IGNORED_NOISE"))).scalars().all()
                    if not trends:
                        await update_agent_telemetry(db, self.agent_id, "MEMORY_ANALYST", "IDLE", "Файл Памяти (0)", "Ожидание первых сигналов...")
                        await asyncio.sleep(15)
                        continue

                    for tr in trends:
                        kpis = TrendMathEngine.calculate_kpis(tr.delta_history or [])
                        tr.velocity = kpis["velocity"]
                        tr.z_score = kpis["z_score"]
                        tr.unique_authors_count = kpis["unique_authors"]
                        tr.acceleration_rate = round(kpis["composite_acceleration"] + len(tr.geo_spread or []) * 0.5, 2)
                        tr.last_analyzed_at = utcnow()
                        if tr.is_in_mainstream_media:
                            tr.lifecycle_status = "MAINSTREAM_PRICED_IN"
                        elif tr.z_score >= 1.5 or len(tr.delta_history or []) >= 2:
                            tr.lifecycle_status = "ACCELERATING"
                    await db.commit()

                    state_sig = "|".join([f"{t.code}:{len(t.delta_history or [])}" for t in sorted(trends, key=lambda x: x.code)])
                    state_hash = hashlib.md5(state_sig.encode("utf-8")).hexdigest()
                    if self.last_state_hash == state_hash:
                        await update_agent_telemetry(db, self.agent_id, "MEMORY_ANALYST", "IDLE", f"Файл Памяти ({len(trends)} трендов)", "Z-Score и производные пересчитаны (Новых дельт нет — 0 токенов)")
                        await asyncio.sleep(20)
                        continue
                    self.last_state_hash = state_hash

                    if len(trends) >= 2:
                        top_t = sorted(trends, key=lambda x: x.acceleration_rate, reverse=True)[:8]
                        dump = "\n".join([f"- [{t.code}] (Z={t.z_score}, V={t.velocity}, Дельт={len(t.delta_history or [])}): {t.title} | {t.core_essence}" for t in top_t])
                        await update_agent_telemetry(db, self.agent_id, "MEMORY_ANALYST", "SYNTHESIZING", f"Файл Памяти ({len(trends)} трендов)", "Сшивка кросс-трендовых инвестиционных гипотез...", inc_items=len(top_t))
                        meta_res = await DynamicModelRouter.ask_agent(db, "memory_analyst", f"Свяжи тренды из Файла Памяти в инвестиционную гипотезу:\n{dump}")
                        codes = meta_res.get("linked_codes") or [t.code for t in top_t[:3]]
                        title = meta_res.get("title") or f"Мета-связка: {' + '.join(codes)} ({top_t[0].title[:35]})"
                        if not (await db.execute(select(MetaInsight).where(MetaInsight.title == title))).scalars().first():
                            db.add(MetaInsight(
                                title=title, linked_trend_codes=codes, pattern_type="PAIN_TO_INVESTMENT",
                                consumer_pain_summary=meta_res.get("consumer_pain_summary", top_t[0].core_essence[:220]),
                                investment_or_product_thesis=meta_res.get("investment_or_product_thesis", f"Фокус на пересечении тем: {', '.join([t.title[:30] for t in top_t[:3]])}"),
                                confidence_score=float(meta_res.get("confidence_score", 0.84))
                            ))
                            await db.commit()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ MemoryAnalyst error: {e}")
            await asyncio.sleep(20)

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
            await db.commit()
        for i in range(1, self.scout_count + 1):
            aid = f"Scout-{i}"
            self.scout_tasks[aid] = asyncio.create_task(AutonomousScout(aid, self).run_forever())
        self.special_tasks = [
            asyncio.create_task(ForemanAgent(self).run_forever()),
            asyncio.create_task(ReanimatorAgent(self).run_forever()),
            asyncio.create_task(MemoryAnalystAgent(self).run_forever()),
        ]

    async def scale_scouts(self, new_count: int):
        new_count = max(1, min(100, new_count))
        old_count = self.scout_count
        self.scout_count = new_count
        if not self.is_running:
            return
        if new_count > old_count:
            for i in range(old_count + 1, new_count + 1):
                aid = f"Scout-{i}"
                if aid not in self.scout_tasks or self.scout_tasks[aid].done():
                    self.scout_tasks[aid] = asyncio.create_task(AutonomousScout(aid, self).run_forever())
        elif new_count < old_count:
            async with AsyncSessionLocal() as db:
                for i in range(new_count + 1, old_count + 1):
                    aid = f"Scout-{i}"
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
            aid = f"Scout-{i}"
            t = self.scout_tasks.get(aid)
            if not t or t.done():
                self.scout_tasks[aid] = asyncio.create_task(AutonomousScout(aid, self).run_forever())
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
                tel.current_action = "Остановлен пользователем"
            await db.commit()

swarm_controller = SwarmController()

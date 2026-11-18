from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, text, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, Base, AsyncSessionLocal, get_db
from app.models import (
    SemanticBlock, Resource, AgentRoleConfig, TrendMemory, 
    ConnectorCredential, AgentTelemetry, SwarmBlackboard, MetaInsight, UserAuditLog
)
from app.seed import seed_initial_data
from app.swarm_worker import swarm_controller, utcnow
from app.math_engine import TrendMathEngine


async def log_user_action(db: AsyncSession, actor: str, action_type: str, target: str, details: str):
    db.add(UserAuditLog(actor=actor, action_type=action_type, target=target, details=details))
    await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE resources ADD COLUMN IF NOT EXISTS last_content_hash VARCHAR"))
        await conn.execute(text("ALTER TABLE trend_memory ADD COLUMN IF NOT EXISTS velocity FLOAT DEFAULT 0.0"))
        await conn.execute(text("ALTER TABLE trend_memory ADD COLUMN IF NOT EXISTS z_score FLOAT DEFAULT 0.0"))
    async with AsyncSessionLocal() as db:
        await seed_initial_data(db)
        await log_user_action(db, "SYSTEM", "SERVER_START", "Проект Улей", "Сервер запущен, все 7 разделов API и POST-шлюз Гермеса активны")
    await swarm_controller.start(scout_count=4)
    yield
    await swarm_controller.stop()


app = FastAPI(title="Проект «Улей». Поиск трендов и API Гермеса", lifespan=lifespan)


# =====================================================================
# СХЕМЫ ДАННЫХ (PYDANTIC)
# =====================================================================
class CredentialUpdateSchema(BaseModel):
    service_name: str
    title: str
    api_key_or_token: str


class RoleConfigSchema(BaseModel):
    role_name: str
    title: str
    provider: str
    model_name: str
    api_base: str | None = None
    temperature: float = 0.2
    system_prompt: str


class BlockCreateSchema(BaseModel):
    name: str
    block_type: str
    description: str = ""


class ResourceCreateSchema(BaseModel):
    block_id: int
    name: str
    url_or_id: str
    connector_type: str
    connector_config: dict = {}
    geo_region: str = "RU"
    tags: list[str] = ["#Custom"]
    temperature: float = 75.0


# =====================================================================
# РАЗДЕЛ 1. ОТКРЫТЫЙ READ/WRITE API ДЛЯ ГЕРМЕСА (POST & GET)
# =====================================================================
@app.post("/api/memory/meta-insights", tags=["1. Hermes Read/Write API"])
@app.post("/api/hermes/ingest-insight", tags=["1. Hermes Read/Write API"])
@app.post("/api/ingest-insight", tags=["1. Hermes Read/Write API"])
@app.post("/api/hermes/ingest", tags=["1. Hermes Read/Write API"])
@app.post("/api/ingest", tags=["1. Hermes Read/Write API"])
@app.post("/api/hermes/insights", tags=["1. Hermes Read/Write API"])
@app.post("/api/insights", tags=["1. Hermes Read/Write API"])
async def hermes_write_meta_insight(
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """Принимает POST-запись инсайта от Гермеса на /api/memory/meta-insights и все пути /api/hermes/*"""
    title = (
        payload.get("title")
        or payload.get("name")
        or payload.get("topic")
        or payload.get("heading")
        or "Инсайт от агента Гермес"
    )
    summary = (
        payload.get("consumer_pain_summary")
        or payload.get("summary")
        or payload.get("description")
        or payload.get("essence")
        or payload.get("content")
        or payload.get("text")
        or str(payload)
    )
    thesis = (
        payload.get("investment_or_product_thesis")
        or payload.get("investment_thesis")
        or payload.get("thesis")
        or payload.get("insight")
        or payload.get("recommendation")
        or payload.get("conclusion")
        or "Структурированный вывод записан агентом Гермес."
    )
    codes = (
        payload.get("linked_trend_codes")
        or payload.get("linked_codes")
        or payload.get("trends")
        or payload.get("codes")
        or []
    )
    if isinstance(codes, str):
        codes = [c.strip() for c in codes.split(",") if c.strip()]

    score = float(payload.get("confidence_score") or payload.get("confidence") or payload.get("score") or 0.9)

    new_insight = MetaInsight(
        title=str(title)[:200],
        linked_trend_codes=codes if isinstance(codes, list) else [],
        pattern_type=str(payload.get("pattern_type", "HERMES_INGEST")),
        consumer_pain_summary=str(summary),
        investment_or_product_thesis=str(thesis),
        confidence_score=score
    )
    db.add(new_insight)
    await db.commit()
    await db.refresh(new_insight)

    client_ip = request.client.host if request.client else "Hermes"
    await log_user_action(db, f"Hermes ({client_ip})", "HERMES_WRITE_INSIGHT", str(title)[:80], "Инсайт успешно записан в таблицу meta_insights")

    return {
        "status": "ok",
        "message": "Insight successfully saved to database",
        "id": new_insight.id,
        "title": new_insight.title
    }


@app.post("/api/memory/trends", tags=["1. Hermes Read/Write API"])
@app.post("/api/hermes/trends", tags=["1. Hermes Read/Write API"])
@app.post("/memory/trends", tags=["1. Hermes Read/Write API"])
async def hermes_write_trend(
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """Разрешает прямую POST-запись нового тренда в таблицу trend_memory"""
    title = str(payload.get("title") or payload.get("name") or "Тренд от Гермеса")[:150]
    essence = str(payload.get("core_essence") or payload.get("essence") or payload.get("summary") or payload.get("text") or title)
    keywords = payload.get("keywords_multilang") or payload.get("keywords") or payload.get("tags") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    count_all = len((await db.execute(select(TrendMemory.id))).scalars().all())
    vec = TrendMathEngine.compute_multilang_vector(f"{title} {' '.join(keywords)}")
    now_iso = utcnow().isoformat()

    new_tr = TrendMemory(
        code=f"HIVE-{count_all + 1:04d}",
        title=title,
        core_essence=essence,
        keywords_multilang=keywords if isinstance(keywords, list) else [],
        embedding=vec,
        origin_geo=str(payload.get("origin_geo", "GLOBAL")),
        geo_spread=payload.get("geo_spread", ["GLOBAL"]),
        lifecycle_status=str(payload.get("lifecycle_status", "ACCELERATING")),
        acceleration_rate=float(payload.get("acceleration_rate", 1.0)),
        delta_history=[{"timestamp": now_iso, "date": utcnow().strftime("%Y-%m-%d %H:%M"), "source": "Hermes", "author": "Hermes", "event": essence[:240]}]
    )
    db.add(new_tr)
    await db.commit()
    await db.refresh(new_tr)

    client_ip = request.client.host if request.client else "Hermes"
    await log_user_action(db, f"Hermes ({client_ip})", "HERMES_WRITE_TREND", new_tr.code, f"Записан тренд: {title[:60]}")
    return {"status": "ok", "code": new_tr.code, "title": new_tr.title}


@app.get("/api/hermes/digest", tags=["1. Hermes Read/Write API"])
@app.get("/api/digest", tags=["1. Hermes Read/Write API"])
async def hermes_get_digest(request: Request, limit: int = 10, only_accelerating: bool = False, db: AsyncSession = Depends(get_db)):
    stmt = select(TrendMemory).where(TrendMemory.lifecycle_status != "IGNORED_NOISE")
    if only_accelerating:
        stmt = stmt.where(TrendMemory.acceleration_rate > 0)
    stmt = stmt.order_by(TrendMemory.acceleration_rate.desc(), TrendMemory.last_updated_at.desc()).limit(limit)
    trends = (await db.execute(stmt)).scalars().all()
    metas = (await db.execute(select(MetaInsight).order_by(MetaInsight.created_at.desc()).limit(limit))).scalars().all()

    client_ip = request.client.host if request.client else "Hermes"
    await log_user_action(db, f"Hermes ({client_ip})", "HERMES_DIGEST_PULL", f"limit={limit}", f"Отдано {len(trends)} трендов и {len(metas)} инсайтов")

    rag_lines = [f"[{t.code}] (Ускорение: {t.acceleration_rate}x, Z={t.z_score}σ, Гео={t.geo_spread}): {t.title} — {t.core_essence}" for t in trends]
    return {
        "project": "Проект Улей. Поиск трендов",
        "trends_count": len(trends),
        "dependencies_count": len(metas),
        "rag_context_for_hermes_prompt": "\n".join(rag_lines),
        "trends": [{
            "code": t.code, "title": t.title, "essence": t.core_essence,
            "keywords": t.keywords_multilang, "status": t.lifecycle_status,
            "acceleration_rate": t.acceleration_rate, "z_score": t.z_score, "velocity": t.velocity,
            "geo_spread": t.geo_spread, "sources_count": len(t.delta_history or []),
            "latest_events": (t.delta_history or [])[-3:]
        } for t in trends],
        "cross_dependencies": [{
            "id": m.id, "title": m.title, "linked_trends": m.linked_trend_codes,
            "summary": m.consumer_pain_summary, "investment_thesis": m.investment_or_product_thesis
        } for m in metas]
    }


@app.get("/api/hermes/search", tags=["1. Hermes Read/Write API"])
@app.get("/api/search", tags=["1. Hermes Read/Write API"])
async def hermes_semantic_rag_search(request: Request, q: str, limit: int = 7, db: AsyncSession = Depends(get_db)):
    q_vec = TrendMathEngine.compute_multilang_vector(q)
    dist_col = TrendMemory.embedding.cosine_distance(q_vec).label("dist")
    stmt = (
        select(TrendMemory, dist_col)
        .where(TrendMemory.lifecycle_status != "IGNORED_NOISE")
        .order_by(dist_col)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    client_ip = request.client.host if request.client else "Hermes"
    await log_user_action(db, f"Hermes ({client_ip})", "HERMES_RAG_SEARCH", f"q='{q}'", f"Найдено {len(rows)} совпадений")

    results = [{
        "code": r[0].code,
        "similarity_score": round(max(0.0, 1.0 - float(r[1] or 0.0)), 3),
        "title": r[0].title,
        "essence": r[0].core_essence,
        "keywords": r[0].keywords_multilang,
        "acceleration_rate": r[0].acceleration_rate,
        "z_score": r[0].z_score,
        "delta_history": (r[0].delta_history or [])[-3:]
    } for r in rows]

    return {
        "query": q,
        "rag_context_for_hermes_prompt": "\n".join([f"[{x['code']}] (Сходство: {x['similarity_score']}): {x['title']} — {x['essence']}" for x in results]),
        "matches": results
    }


# =====================================================================
# РАЗДЕЛ 2. УПРАВЛЕНИЕ РОЕМ, ТЕЛЕМЕТРИЯ И ДОСКА ТАНЦЕВ (BLACKBOARD)
# =====================================================================
@app.get("/api/swarm/status", tags=["2. Управление Роем"])
async def get_swarm_status(db: AsyncSession = Depends(get_db)):
    agents = (await db.execute(select(AgentTelemetry).order_by(AgentTelemetry.agent_type, AgentTelemetry.agent_id))).scalars().all()
    blackboard = (await db.execute(select(SwarmBlackboard).where(SwarmBlackboard.status == "ACTIVE").order_by(SwarmBlackboard.created_at.desc()).limit(10))).scalars().all()
    return {
        "is_running": swarm_controller.is_running,
        "scout_count": swarm_controller.scout_count,
        "agents": agents,
        "blackboard": [{
            "id": b.id,
            "source_resource_name": b.source_resource_name,
            "signal_summary": b.signal_summary,
            "search_queries": b.search_queries,
            "target_block_types": b.target_block_types,
            "checked_by_blocks": b.checked_by_blocks,
            "priority_boost": b.priority_boost
        } for b in blackboard]
    }


@app.post("/api/swarm/toggle", tags=["2. Управление Роем"])
async def toggle_swarm(request: Request, run: bool, db: AsyncSession = Depends(get_db)):
    if run:
        await swarm_controller.start(scout_count=swarm_controller.scout_count)
    else:
        await swarm_controller.stop()
    await log_user_action(db, request.client.host, "SWARM_TOGGLE", "Рой Улья", f"Статус роя: {'ВЫЛЕТ' if run else 'ПАУЗА'}")
    return {"is_running": swarm_controller.is_running}


@app.post("/api/swarm/scale", tags=["2. Управление Роем"])
async def scale_swarm_scouts(request: Request, scout_count: int, db: AsyncSession = Depends(get_db)):
    old_c = swarm_controller.scout_count
    await swarm_controller.scale_scouts(scout_count)
    await log_user_action(db, request.client.host, "SWARM_SCALE", "Штат Пчёл", f"Разведчиц: {old_c} -> {swarm_controller.scout_count}")
    return {"status": "scaled", "scout_count": swarm_controller.scout_count}


# =====================================================================
# РАЗДЕЛ 3. СОТЫ ПАМЯТИ И МЕТА-ИНСАЙТЫ
# =====================================================================
@app.get("/api/memory/trends", tags=["3. Соты Памяти"])
@app.get("/memory/trends", tags=["3. Соты Памяти"])
async def get_trends(status: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(TrendMemory).order_by(TrendMemory.acceleration_rate.desc(), TrendMemory.last_updated_at.desc())
    if status:
        stmt = stmt.where(TrendMemory.lifecycle_status == status)
    else:
        stmt = stmt.where(TrendMemory.lifecycle_status != "IGNORED_NOISE")
    rows = (await db.execute(stmt)).scalars().all()
    return [{
        "code": r.code, "title": r.title, "core_essence": r.core_essence,
        "keywords_multilang": r.keywords_multilang, "lifecycle_status": r.lifecycle_status,
        "origin_geo": r.origin_geo, "geo_spread": r.geo_spread,
        "unique_authors_count": r.unique_authors_count, "velocity": r.velocity,
        "z_score": r.z_score, "acceleration_rate": r.acceleration_rate,
        "is_in_mainstream_media": r.is_in_mainstream_media, "delta_history": r.delta_history
    } for r in rows]


@app.post("/api/memory/clear", tags=["3. Соты Памяти"])
async def clear_memory_comb(request: Request, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(TrendMemory))
    await db.execute(delete(MetaInsight))
    await db.execute(delete(SwarmBlackboard))
    await log_user_action(db, request.client.host, "MEMORY_CLEAR", "Соты Памяти", "Очистка накопленных трендов и инсайтов")
    return {"status": "cleared"}


@app.get("/api/memory/meta-insights", tags=["3. Соты Памяти"])
@app.get("/api/hermes/insights", tags=["1. Hermes Read/Write API"])
@app.get("/api/insights", tags=["1. Hermes Read/Write API"])
async def get_meta_insights(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(MetaInsight).order_by(MetaInsight.created_at.desc()))).scalars().all()


@app.patch("/api/memory/trends/{code}", tags=["3. Соты Памяти"])
@app.patch("/memory/trends/{code}/status", tags=["3. Соты Памяти"])
async def change_trend_status(request: Request, code: str, status: str = "IGNORED_NOISE", db: AsyncSession = Depends(get_db)):
    tr = (await db.execute(select(TrendMemory).where(TrendMemory.code == code))).scalars().first()
    if not tr:
        raise HTTPException(404)
    tr.lifecycle_status = status
    await log_user_action(db, request.client.host, "TREND_STATUS_CHANGE", code, f"Статус изменен на {status}")
    return {"status": "ok"}


# =====================================================================
# РАЗДЕЛ 4. СМЫСЛОВЫЕ БЛОКИ, 120+ РЕСУРСОВ И КЛЮЧИ КОННЕКТОРОВ
# =====================================================================
@app.get("/api/resources", tags=["4. Ресурсы и Коннекторы"])
@app.get("/admin/resources/heatmap", tags=["4. Ресурсы и Коннекторы"])
async def get_all_resources(db: AsyncSession = Depends(get_db)):
    blocks = (await db.execute(select(SemanticBlock))).scalars().all()
    resources = (await db.execute(select(Resource).order_by(Resource.temperature.desc()))).scalars().all()
    creds = (await db.execute(select(ConnectorCredential))).scalars().all()
    masked_creds = []
    for c in creds:
        val = c.api_key_or_token or ""
        if val and not val.startswith("http"):
            val = "***" + val[-4:] if len(val) > 4 else "***"
        masked_creds.append({"service_name": c.service_name, "title": c.title, "api_key_or_token": val})
    return {"blocks": blocks, "resources": resources, "credentials": masked_creds}


@app.post("/api/blocks", tags=["4. Ресурсы и Коннекторы"])
@app.post("/admin/blocks", tags=["4. Ресурсы и Коннекторы"])
async def create_semantic_block(request: Request, payload: BlockCreateSchema, db: AsyncSession = Depends(get_db)):
    new_b = SemanticBlock(**payload.model_dump())
    db.add(new_b)
    await db.commit()
    await db.refresh(new_b)
    await log_user_action(db, request.client.host, "BLOCK_CREATE", new_b.name, f"Создан смысловой блок [{new_b.block_type}]")
    return new_b


@app.post("/api/resources", tags=["4. Ресурсы и Коннекторы"])
@app.post("/admin/resources", tags=["4. Ресурсы и Коннекторы"])
async def add_new_resource(request: Request, payload: ResourceCreateSchema, db: AsyncSession = Depends(get_db)):
    new_r = Resource(**payload.model_dump())
    db.add(new_r)
    await db.commit()
    await db.refresh(new_r)
    await log_user_action(db, request.client.host, "RESOURCE_ADD", new_r.name, f"Добавлен ресурс {new_r.url_or_id} ({new_r.connector_type})")
    return {"status": "added", "id": new_r.id}


@app.patch("/api/resources/{res_id}/toggle", tags=["4. Ресурсы и Коннекторы"])
async def toggle_resource(request: Request, res_id: int, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(Resource).where(Resource.id == res_id))).scalars().first()
    if r:
        r.is_active = not r.is_active
        await log_user_action(db, request.client.host, "RESOURCE_TOGGLE", r.name, f"Ресурс {'включен' if r.is_active else 'выключен'}")
    return {"status": "ok"}


@app.delete("/api/resources/{res_id}", tags=["4. Ресурсы и Коннекторы"])
async def delete_resource(request: Request, res_id: int, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(Resource).where(Resource.id == res_id))).scalars().first()
    if r:
        name = r.name
        await db.delete(r)
        await log_user_action(db, request.client.host, "RESOURCE_DELETE", name, "Ресурс удален пользователем")
    return {"status": "deleted"}


@app.post("/api/credentials", tags=["4. Ресурсы и Коннекторы"])
async def save_credential(request: Request, payload: CredentialUpdateSchema, db: AsyncSession = Depends(get_db)):
    if payload.api_key_or_token.startswith("***"):
        return {"status": "unchanged"}
    c = (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == payload.service_name))).scalars().first()
    if c:
        c.api_key_or_token = payload.api_key_or_token
    else:
        db.add(ConnectorCredential(**payload.model_dump()))
    await log_user_action(db, request.client.host, "CREDENTIAL_UPDATE", payload.service_name, f"Обновлен доступ: {payload.title}")
    return {"status": "saved"}


# =====================================================================
# РАЗДЕЛ 5. УПРАВЛЕНИЕ МОДЕЛЯМИ И ПРОМПТАМИ РОЛЕЙ
# =====================================================================
@app.get("/api/models", tags=["5. Модели и Промпты"])
@app.get("/admin/models", tags=["5. Модели и Промпты"])
async def get_models(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(AgentRoleConfig))).scalars().all()


@app.post("/api/models", tags=["5. Модели и Промпты"])
@app.post("/admin/models", tags=["5. Модели и Промпты"])
async def save_model(request: Request, payload: RoleConfigSchema, db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == payload.role_name))).scalars().first()
    if m:
        for k, v in payload.model_dump().items():
            setattr(m, k, v)
    else:
        db.add(AgentRoleConfig(**payload.model_dump()))
    await log_user_action(db, request.client.host, "MODEL_PROMPT_UPDATE", payload.role_name, f"Обновлена модель/промпт ({payload.provider}/{payload.model_name})")
    return {"status": "saved"}


# =====================================================================
# РАЗДЕЛ 6. ЛОГИ ДЕЙСТВИЙ ПОЛЬЗОВАТЕЛЕЙ И ЗАПРОСОВ ГЕРМЕСА
# =====================================================================
@app.get("/api/audit-logs", tags=["6. Audit Log"])
async def get_audit_logs(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(UserAuditLog).order_by(UserAuditLog.created_at.desc()).limit(150))).scalars().all()


# =====================================================================
# РАЗДЕЛ 7. ВЕБ-ПАНЕЛЬ УПРАВЛЕНИЯ УЛЬЕМ (6 ВКЛАДОК + ДОСКА ТАНЦЕВ + ДОБАВЛЕНИЕ URL)
# =====================================================================
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    return '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Проект «Улей». Поиск трендов</title>
    <style>
        body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; background: #1e293b; padding: 15px 25px; border-radius: 10px; margin-bottom: 20px; border-bottom: 2px solid #f59e0b; }
        .btn { padding: 8px 14px; border-radius: 6px; border: none; cursor: pointer; font-weight: 600; color: white; }
        .btn-green { background: #10b981; } .btn-red { background: #ef4444; } .btn-blue { background: #f59e0b; color: #0f172a; }
        .tabs { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
        .tab { padding: 10px 16px; background: #1e293b; border-radius: 8px; cursor: pointer; font-size: 13.5px; }
        .tab.active { background: #f59e0b; color: #0f172a; font-weight: bold; }
        .card { background: #1e293b; padding: 16px; border-radius: 8px; margin-bottom: 12px; border-left: 4px solid #f59e0b; line-height: 1.5; }
        table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; margin-bottom: 18px; }
        th, td { padding: 11px; text-align: left; border-bottom: 1px solid #334155; font-size: 13px; }
        th { background: #0f172a; }
        input, select, textarea { width: 100%; padding: 8px; margin-top: 5px; margin-bottom: 10px; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 6px; box-sizing: border-box; }
        .badge { padding: 4px 9px; border-radius: 4px; font-size: 11px; font-weight: bold; background: #334155; display: inline-block; margin-right: 4px; }
        .badge-role { background: #d97706; color: #fff; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h2 style="margin:0;">🐝 Проект «Улей». Поиск трендов и направлений инвестиций</h2>
            <small id="swarm-indicator">Загрузка состояния Улья...</small>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
            <span style="font-size:13px;">Разведчиц и Фуражиров (1-100):</span>
            <input type="number" id="scout-count-input" value="4" min="1" max="100" style="width:65px; margin:0; text-align:center;">
            <button class="btn btn-blue" onclick="scaleSwarm()">⚡ Изменить рой</button>
            <button class="btn btn-green" onclick="toggleSwarm(true)">▶ Вылет</button>
            <button class="btn btn-red" onclick="toggleSwarm(false)">⏹ В улей</button>
        </div>
    </div>

    <div class="tabs">
        <div class="tab active" onclick="showTab('tab-agents', this)">1. Жизнь Улья & Доска Танцев</div>
        <div class="tab" onclick="showTab('tab-meta', this)">2. Выявленные Зависимости & Инсайты Гермеса</div>
        <div class="tab" onclick="showTab('tab-memory', this)">3. Соты Памяти (Базовый замер vs Ускорение)</div>
        <div class="tab" onclick="showTab('tab-resources', this)">4. Поле 120+ URL & Добавление ресурсов</div>
        <div class="tab" onclick="showTab('tab-models', this)">5. Роли и Промпты</div>
        <div class="tab" onclick="showTab('tab-logs', this)">6. Логи действий пользователей</div>
    </div>

    <!-- ВКЛАДКА 1: АГЕНТЫ И ДОСКА ТАНЦЕВ -->
    <div id="tab-agents" class="tab-content">
        <table>
            <thead><tr><th>Пчела</th><th>Каста в Улье</th><th>Статус</th><th>Текущий узел / Ресурс</th><th>Выполняемая операция</th><th>Обработано</th></tr></thead>
            <tbody id="agents-tbody"></tbody>
        </table>
        <h4>💃 Активные сигнальные танцы на Доске Улья (Очередь перекрестной проверки для Фуражиров):</h4>
        <div id="blackboard-container"></div>
    </div>

    <!-- ВКЛАДКА 2: ЗАВИСИМОСТИ И ГЕРМЕС -->
    <div id="tab-meta" class="tab-content" style="display:none;">
        <div class="card" style="border-left-color:#3b82f6; margin-bottom:16px;">
            <b>🔗 POST/GET API для агента Гермес открыт на порту :8000:</b><br>
            <small style="color:#94a3b8;">
                &bull; Запись инсайта (POST JSON): <code>POST /api/memory/meta-insights</code> (или <code>/api/hermes/ingest-insight</code>, <code>/api/ingest</code>)<br>
                &bull; Запись тренда (POST JSON): <code>POST /api/memory/trends</code><br>
                &bull; Чтение дайджеста: <code>GET /api/hermes/digest?limit=10</code> | Поиск: <code>GET /api/hermes/search?q=запрос</code>
            </small>
        </div>
        <div id="meta-container"></div>
    </div>

    <!-- ВКЛАДКА 3: СОТЫ ПАМЯТИ -->
    <div id="tab-memory" class="tab-content" style="display:none;">
        <div style="margin-bottom:12px; display:flex; justify-content:space-between; align-items:center;">
            <span><b>Математика Пчёл-Приёмщиц:</b> 1-е упоминание = <code>ЗАМЕР БАЗЫ (0.0x)</code>. Ускорение растет при превышении базовой частоты.</span>
            <button class="btn btn-red" onclick="clearMemory()">🧹 Очистить соты памяти</button>
        </div>
        <div id="trends-container"></div>
    </div>

    <!-- ВКЛАДКА 4: РЕСУРСЫ, КЛЮЧИ И ФОРМА ДОБАВЛЕНИЯ НОВОГО URL -->
    <div id="tab-resources" class="tab-content" style="display:none;">
        <div id="creds-container"></div>
        <div class="card" style="border-left-color:#10b981;">
            <h4 style="margin-top:0;">➕ Добавить новый ресурс (URL) в Улей:</h4>
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:10px;">
                <div>Смысловой блок:<select id="new-res-block"></select></div>
                <div>Название ресурса:<input id="new-res-name" placeholder="Напр: Форум биохакеров"></div>
                <div>URL или API-адрес:<input id="new-res-url" placeholder="https://..."></div>
                <div>Тип коннектора:
                    <select id="new-res-type">
                        <option value="rss_feed">rss_feed (RSS / Atom)</option>
                        <option value="html_scraper">html_scraper (HTML по селектору)</option>
                        <option value="telegram_web">telegram_web (t.me/s/...)</option>
                        <option value="json_api">json_api (Reddit / 2ch JSON)</option>
                        <option value="hh_vacancies">hh_vacancies (API HH.ru)</option>
                        <option value="wb_mpstats">wb_mpstats (WB / MPStats)</option>
                    </select>
                </div>
                <div>Гео-регион:<input id="new-res-geo" value="RU"></div>
                <div>Теги (через запятую):<input id="new-res-tags" value="#Custom, #Trends"></div>
            </div>
            <button class="btn btn-green" onclick="addNewResource()">➕ Добавить ресурс в ротацию</button>
        </div>
        <h3>🌺 Поле ресурсов (<span id="res-count">0</span> URL — равномерный облет сверху донизу):</h3>
        <table>
            <thead><tr><th>Температура</th><th>Ресурс и URL</th><th>Теги и Гео</th><th>Коннектор</th><th>Здоровье</th><th>Кто на цветке</th><th>Действия</th></tr></thead>
            <tbody id="resources-tbody"></tbody>
        </table>
    </div>

    <!-- ВКЛАДКА 5: МОДЕЛИ И ПРОМПТЫ -->
    <div id="tab-models" class="tab-content" style="display:none;"><div id="models-container"></div></div>

    <!-- ВКЛАДКА 6: ЛОГИ -->
    <div id="tab-logs" class="tab-content" style="display:none;">
        <h3>📋 Журнал действий пользователей и запросов Гермеса (Audit Log):</h3>
        <table>
            <thead><tr><th>Время (UTC)</th><th>Субъект (IP / Бот)</th><th>Тип события</th><th>Объект</th><th>Детали операции</th></tr></thead>
            <tbody id="logs-tbody"></tbody>
        </table>
    </div>

<script>
    function showTab(id, el) {
        document.querySelectorAll('.tab-content').forEach(d => d.style.display = 'none');
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.getElementById(id).style.display = 'block';
        el.classList.add('active');
        if(id === 'tab-meta') loadMetaInsights();
        if(id === 'tab-memory') loadTrends();
        if(id === 'tab-resources') loadResources();
        if(id === 'tab-models') loadModels();
        if(id === 'tab-logs') loadAuditLogs();
    }
    async function toggleSwarm(run) { await fetch('/api/swarm/toggle?run=' + run, {method: 'POST'}); loadSwarmStatus(); }
    async function scaleSwarm() {
        const c = document.getElementById('scout-count-input').value;
        await fetch('/api/swarm/scale?scout_count=' + c, {method: 'POST'});
        loadSwarmStatus();
    }
    async function clearMemory() {
        if(confirm('Очистить все записи в Сотах Памяти?')) {
            await fetch('/api/memory/clear', {method: 'POST'});
            loadTrends();
        }
    }
    async function loadSwarmStatus() {
        const res = await fetch('/api/swarm/status');
        const data = await res.json();
        document.getElementById('swarm-indicator').innerHTML = data.is_running 
            ? `<span style="color:#10b981;">● УЛЕЙ АКТИВЕН 24/7 (Разведчиц и Фуражиров: ${data.scout_count} + Королева + Приёмщица + Страж)</span>` 
            : '<span style="color:#ef4444;">● УЛЕЙ НА ПАУЗЕ</span>';
        document.getElementById('agents-tbody').innerHTML = data.agents.map(a => `
            <tr><td><b>🐝 ${a.agent_id}</b></td><td><span class="badge badge-role">${a.agent_type}</span></td><td><span class="badge">${a.state}</span></td><td>${a.current_resource}</td><td>${a.current_action}</td><td>${a.items_processed}</td></tr>
        `).join('');
        const bb = data.blackboard || [];
        document.getElementById('blackboard-container').innerHTML = bb.length ? bb.map(b => `
            <div class="card" style="padding:10px 14px;">
                <b>Источник танца: ${b.source_resource_name}</b> <span class="badge">Ключи: ${(b.search_queries || []).join(', ')}</span>
                <span class="badge">Проверено блоков: ${(b.checked_by_blocks || []).length} / ${(b.target_block_types || []).length}</span>
                <div style="font-size:12.5px; color:#cbd5e1; margin-top:4px;">${b.signal_summary}</div>
            </div>
        `).join('') : '<small style="color:#94a3b8;">Все текущие сигнальные танцы уже разобраны Фуражирами и переданы в Соты Памяти.</small>';
    }
    async function loadMetaInsights() {
        const items = await (await fetch('/api/memory/meta-insights')).json();
        document.getElementById('meta-container').innerHTML = items.length ? items.map(m => `
            <div class="card" style="border-left-color:#10b981;">
                <b>💡 ${m.title}</b> <span class="badge">${m.pattern_type}</span> <span class="badge">Связанные соты: ${(m.linked_trend_codes || []).join(', ')}</span>
                <p style="white-space:pre-line;"><b>Суть / Контекст:</b>\n${m.consumer_pain_summary}</p>
                <p style="color:#34d399;"><b>Инвестиционный / продуктовый вывод:</b> ${m.investment_or_product_thesis}</p>
            </div>
        `).join('') : '<p>Ожидание записи инсайтов от Гермеса или Пчелы-Приёмщицы...</p>';
    }
    async function loadTrends() {
        const trends = await (await fetch('/api/memory/trends')).json();
        document.getElementById('trends-container').innerHTML = trends.length ? trends.map(t => {
            const statusLabel = t.lifecycle_status === 'CALIBRATING_BASELINE' ? '⏳ ЗАМЕР БАЗЫ (0.0x)' :
                                t.lifecycle_status === 'ACCELERATING' ? '🔥 УСКОРЯЕТСЯ К БАЗЕ' :
                                t.lifecycle_status === 'MAINSTREAM_PRICED_IN' ? '📰 УЖЕ В СМИ' : t.lifecycle_status;
            return `
            <div class="card">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <b>[${t.code}] ${t.title}</b>
                    <div>
                        <span class="badge" style="background:${t.acceleration_rate > 0 ? '#10b981' : '#475569'}">⚡ Ускорение: ${t.acceleration_rate}x (Z: ${t.z_score || 0}σ | V: ${t.velocity || 0})</span>
                        <span class="badge">${statusLabel}</span>
                        <span class="badge">Гео: ${(t.geo_spread || []).join(', ')}</span>
                        <button class="btn btn-red" style="padding:3px 8px; font-size:11px;" onclick="ignoreTrend('${t.code}')">В игнор</button>
                    </div>
                </div>
                <p style="white-space:pre-line; margin:10px 0;">${t.core_essence}</p>
                <small style="color:#94a3b8;">Ключевые термины: ${(t.keywords_multilang || []).join(', ')} | Замеров в истории: ${(t.delta_history || []).length} (Источник: ${t.delta_history && t.delta_history[0] ? t.delta_history[0].source : '-'})</small>
            </div>`;
        }).join('') : '<p>Соты Памяти чисты. Пчёлы-Разведчицы собирают данные...</p>';
    }
    async function ignoreTrend(code) { await fetch(`/api/memory/trends/${code}?status=IGNORED_NOISE`, {method: 'PATCH'}); loadTrends(); }
    async function loadResources() {
        const data = await (await fetch('/api/resources')).json();
        document.getElementById('res-count').innerText = data.resources.length;
        if (data.blocks && data.blocks.length) {
            document.getElementById('new-res-block').innerHTML = data.blocks.map(b => `<option value="${b.id}">${b.name}</option>`).join('');
        }
        document.getElementById('creds-container').innerHTML = data.credentials.map(c => `
            <div class="card" style="display:flex; gap:10px; align-items:center;">
                <div style="flex:1;"><b>${c.title}</b></div>
                <input id="cred-${c.service_name}" value="${c.api_key_or_token}" placeholder="URL локальной модели или токен..." style="margin:0; flex:2;">
                <button class="btn btn-blue" onclick="saveCred('${c.service_name}', '${c.title}')">Сохранить</button>
            </div>
        `).join('');
        document.getElementById('resources-tbody').innerHTML = data.resources.map(r => `
            <tr><td>🍯 <b>${r.temperature}°</b></td><td><b>${r.name}</b><br><small style="color:#94a3b8;">${r.url_or_id}</small></td>
            <td><span class="badge badge-role">${r.geo_region}</span> ${(r.tags || []).map(t => `<span class="badge">${t}</span>`).join('')}</td>
            <td><span class="badge">${r.connector_type}</span></td><td><span class="badge">${r.health_status}</span></td>
            <td>${r.locked_by_agent || '-'}</td>
            <td style="display:flex; gap:6px;">
                <button class="btn ${r.is_active ? 'btn-green' : 'btn-red'}" onclick="toggleRes(${r.id})">${r.is_active ? 'Вкл' : 'Выкл'}</button>
                <button class="btn btn-red" onclick="deleteRes(${r.id})">✕</button>
            </td></tr>
        `).join('');
    }
    async function addNewResource() {
        const tagsRaw = document.getElementById('new-res-tags').value;
        const payload = {
            block_id: parseInt(document.getElementById('new-res-block').value),
            name: document.getElementById('new-res-name').value,
            url_or_id: document.getElementById('new-res-url').value,
            connector_type: document.getElementById('new-res-type').value,
            geo_region: document.getElementById('new-res-geo').value || 'RU',
            tags: tagsRaw.split(',').map(t => t.trim()).filter(Boolean)
        };
        await fetch('/api/resources', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
        loadResources();
    }
    async function deleteRes(id) {
        if(confirm('Удалить этот ресурс из базы?')) {
            await fetch(`/api/resources/${id}`, {method:'DELETE'});
            loadResources();
        }
    }
    async function saveCred(service_name, title) {
        const val = document.getElementById('cred-' + service_name).value;
        await fetch('/api/credentials', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({service_name, title, api_key_or_token: val})});
        alert('Сохранено!');
    }
    async function toggleRes(id) { await fetch(`/api/resources/${id}/toggle`, {method:'PATCH'}); loadResources(); }
    async function loadModels() {
        const models = await (await fetch('/api/models')).json();
        document.getElementById('models-container').innerHTML = models.map(m => `
            <div class="card"><h4>${m.title} (${m.role_name})</h4>
            Провайдер (ollama / vllm / openrouter): <input id="prov-${m.role_name}" value="${m.provider}">
            Модель: <input id="mod-${m.role_name}" value="${m.model_name}">
            Промпт: <textarea id="pr-${m.role_name}" rows="6">${m.system_prompt}</textarea>
            <button class="btn btn-blue" onclick="saveModel('${m.role_name}', '${m.title}')">Сохранить на лету</button></div>
        `).join('');
    }
    async function saveModel(role_name, title) {
        await fetch('/api/models', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
            role_name, title, provider: document.getElementById('prov-' + role_name).value,
            model_name: document.getElementById('mod-' + role_name).value, system_prompt: document.getElementById('pr-' + role_name).value
        })});
        alert('Обновлено!');
    }
    async function loadAuditLogs() {
        const logs = await (await fetch('/api/audit-logs')).json();
        document.getElementById('logs-tbody').innerHTML = logs.map(l => `
            <tr>
                <td><small>${new Date(l.created_at).toLocaleString('ru-RU')}</small></td>
                <td><b>${l.actor}</b></td>
                <td><span class="badge badge-role">${l.action_type}</span></td>
                <td>${l.target}</td>
                <td>${l.details}</td>
            </tr>
        `).join('');
    }
    setInterval(() => { if (!document.hidden) loadSwarmStatus(); }, 6000);
    loadSwarmStatus();
</script>
</body>
</html>'''

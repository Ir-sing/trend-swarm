from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, text, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, Base, AsyncSessionLocal, get_db
from app.models import (
    SemanticBlock, Resource, AgentRoleConfig, TrendMemory, 
    ConnectorCredential, AgentTelemetry, SwarmBlackboard, MetaInsight, UserAuditLog
)
from app.seed import seed_initial_data
from app.swarm_worker import swarm_controller
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
        await log_user_action(db, "SYSTEM", "SERVER_START", "Проект Улей", "Контейнер запущен, схема БД синхронизирована")
    await swarm_controller.start(scout_count=4)
    yield
    await swarm_controller.stop()

app = FastAPI(title="Проект «Улей». Поиск трендов и RAG-шлюз Гермеса", lifespan=lifespan)

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

class HermesIngestSchema(BaseModel):
    title: str
    linked_trend_codes: list[str] = []
    consumer_pain_summary: str
    investment_or_product_thesis: str
    confidence_score: float = 0.9

# =====================================================================
# ШЛЮЗ ДЛЯ ВНЕШНЕГО АГЕНТА ГЕРМЕС (ТЕЛЕГРАМ- БОТ / СКИЛЛ НА ПОРТУ :8000)
# =====================================================================
@app.get("/api/hermes/digest", tags=["Hermes Telegram Bridge"])
async def hermes_get_digest(request: Request, limit: int = 10, only_accelerating: bool = False, db: AsyncSession = Depends(get_db)):
    """Выгрузка структурированных трендов и зависимостей для слеш-команды Гермеса из Telegram"""
    stmt = select(TrendMemory).where(TrendMemory.lifecycle_status != "IGNORED_NOISE")
    if only_accelerating:
        stmt = stmt.where(TrendMemory.acceleration_rate > 0)
    stmt = stmt.order_by(TrendMemory.acceleration_rate.desc(), TrendMemory.last_updated_at.desc()).limit(limit)
    trends = (await db.execute(stmt)).scalars().all()
    metas = (await db.execute(select(MetaInsight).order_by(MetaInsight.created_at.desc()).limit(limit))).scalars().all()

    await log_user_action(db, f"Hermes-Bot ({request.client.host})", "HERMES_DIGEST_PULL", f"limit={limit}", f"Выгружено {len(trends)} трендов и {len(metas)} зависимостей")

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
            "title": m.title, "linked_trends": m.linked_trend_codes,
            "summary": m.consumer_pain_summary, "investment_thesis": m.investment_or_product_thesis
        } for m in metas]
    }

@app.get("/api/hermes/search", tags=["Hermes Telegram Bridge"])
async def hermes_semantic_rag_search(request: Request, q: str, limit: int = 7, db: AsyncSession = Depends(get_db)):
    """Векторный (pgvector) + ключевой RAG-поиск по Сотам Памяти по контекстному запросу из Telegram"""
    q_vec = TrendMathEngine.compute_multilang_vector(q)
    dist_col = TrendMemory.embedding.cosine_distance(q_vec).label("dist")
    stmt = (
        select(TrendMemory, dist_col)
        .where(TrendMemory.lifecycle_status != "IGNORED_NOISE")
        .order_by(dist_col)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    await log_user_action(db, f"Hermes-Bot ({request.client.host})", "HERMES_RAG_SEARCH", f"q='{q}'", f"Найдено {len(rows)} семантических совпадений в pgvector")

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

@app.post("/api/hermes/ingest-insight", tags=["Hermes Telegram Bridge"])
async def hermes_ingest_insight(request: Request, payload: HermesIngestSchema, db: AsyncSession = Depends(get_db)):
    """Позволяет внешнему Гермесу сохранить свой структурированный вывод обратно во вкладку Зависимостей Улья"""
    db.add(MetaInsight(
        title=payload.title,
        linked_trend_codes=payload.linked_trend_codes,
        pattern_type="HERMES_SYNTHESIS",
        consumer_pain_summary=payload.consumer_pain_summary,
        investment_or_product_thesis=payload.investment_or_product_thesis,
        confidence_score=payload.confidence_score
    ))
    await log_user_action(db, f"Hermes-Bot ({request.client.host})", "HERMES_INGEST", payload.title, "Внешний Гермес записал структурированный вывод в Улей")
    return {"status": "saved_to_hive"}

# =====================================================================
# ВНУТРЕННИЕ ЭНДПОИНТЫ ПАНЕЛИ УПРАВЛЕНИЯ С ЗАПИСЬЮ В AUDIT LOG
# =====================================================================
@app.get("/api/swarm/status")
async def get_swarm_status(db: AsyncSession = Depends(get_db)):
    agents = (await db.execute(select(AgentTelemetry).order_by(AgentTelemetry.agent_type, AgentTelemetry.agent_id))).scalars().all()
    return {"is_running": swarm_controller.is_running, "scout_count": swarm_controller.scout_count, "agents": agents}

@app.post("/api/swarm/toggle")
async def toggle_swarm(request: Request, run: bool, db: AsyncSession = Depends(get_db)):
    if run:
        await swarm_controller.start(scout_count=swarm_controller.scout_count)
    else:
        await swarm_controller.stop()
    await log_user_action(db, request.client.host, "SWARM_TOGGLE", "Рой Улья", f"Переключен статус роя: {'ВЫЛЕТ (RUN)' if run else 'ПАУЗА (STOP)'}")
    return {"is_running": swarm_controller.is_running}

@app.post("/api/swarm/scale")
async def scale_swarm_scouts(request: Request, scout_count: int, db: AsyncSession = Depends(get_db)):
    old_c = swarm_controller.scout_count
    await swarm_controller.scale_scouts(scout_count)
    await log_user_action(db, request.client.host, "SWARM_SCALE", "Штат Пчёл-Разведчиц", f"Изменено количество разведчиц: {old_c} -> {swarm_controller.scout_count}")
    return {"status": "scaled", "scout_count": swarm_controller.scout_count}

@app.get("/api/memory/trends")
async def get_trends(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(TrendMemory).where(TrendMemory.lifecycle_status != "IGNORED_NOISE").order_by(TrendMemory.acceleration_rate.desc(), TrendMemory.last_updated_at.desc()))).scalars().all()
    return [{
        "code": r.code, "title": r.title, "core_essence": r.core_essence,
        "keywords_multilang": r.keywords_multilang, "lifecycle_status": r.lifecycle_status,
        "origin_geo": r.origin_geo, "geo_spread": r.geo_spread,
        "unique_authors_count": r.unique_authors_count, "velocity": r.velocity,
        "z_score": r.z_score, "acceleration_rate": r.acceleration_rate,
        "is_in_mainstream_media": r.is_in_mainstream_media, "delta_history": r.delta_history
    } for r in rows]

@app.post("/api/memory/clear")
async def clear_memory_comb(request: Request, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(TrendMemory))
    await db.execute(delete(MetaInsight))
    await db.execute(delete(SwarmBlackboard))
    await log_user_action(db, request.client.host, "MEMORY_CLEAR", "Соты Памяти", "Полная очистка накопленных трендов и зависимостей пользователем")
    return {"status": "cleared"}

@app.get("/api/memory/meta-insights")
async def get_meta_insights(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(MetaInsight).order_by(MetaInsight.created_at.desc()))).scalars().all()

@app.patch("/api/memory/trends/{code}")
async def change_trend_status(request: Request, code: str, status: str, db: AsyncSession = Depends(get_db)):
    tr = (await db.execute(select(TrendMemory).where(TrendMemory.code == code))).scalars().first()
    if not tr:
        raise HTTPException(404)
    tr.lifecycle_status = status
    await log_user_action(db, request.client.host, "TREND_STATUS_CHANGE", code, f"Тренд '{tr.title[:50]}' переведен в статус {status}")
    return {"status": "ok"}

@app.get("/api/resources")
async def get_all_resources(db: AsyncSession = Depends(get_db)):
    resources = (await db.execute(select(Resource).order_by(Resource.temperature.desc()))).scalars().all()
    creds = (await db.execute(select(ConnectorCredential))).scalars().all()
    masked_creds = []
    for c in creds:
        val = c.api_key_or_token or ""
        # URL локальной модели оставляем видимым, а секретные токены маскируем
        if val and not val.startswith("http"):
            val = "***" + val[-4:] if len(val) > 4 else "***"
        masked_creds.append({"service_name": c.service_name, "title": c.title, "api_key_or_token": val})
    return {"resources": resources, "credentials": masked_creds}

@app.patch("/api/resources/{res_id}/toggle")
async def toggle_resource(request: Request, res_id: int, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(Resource).where(Resource.id == res_id))).scalars().first()
    if r:
        r.is_active = not r.is_active
        await log_user_action(db, request.client.host, "RESOURCE_TOGGLE", r.name, f"Ресурс {'включен' if r.is_active else 'выключен'}")
    return {"status": "ok"}

@app.post("/api/credentials")
async def save_credential(request: Request, payload: CredentialUpdateSchema, db: AsyncSession = Depends(get_db)):
    if payload.api_key_or_token.startswith("***"):
        return {"status": "unchanged"}
    c = (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == payload.service_name))).scalars().first()
    if c:
        c.api_key_or_token = payload.api_key_or_token
    await log_user_action(db, request.client.host, "CREDENTIAL_UPDATE", payload.service_name, f"Обновлены параметры доступа для {payload.title}")
    return {"status": "saved"}

@app.get("/api/models")
async def get_models(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(AgentRoleConfig))).scalars().all()

@app.post("/api/models")
async def save_model(request: Request, payload: RoleConfigSchema, db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == payload.role_name))).scalars().first()
    if m:
        for k, v in payload.model_dump().items():
            setattr(m, k, v)
        await log_user_action(db, request.client.host, "MODEL_PROMPT_UPDATE", payload.role_name, f"Обновлена модель ({payload.provider}/{payload.model_name}) или промпт роли")
    return {"status": "saved"}

@app.get("/api/audit-logs")
async def get_audit_logs(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(UserAuditLog).order_by(UserAuditLog.created_at.desc()).limit(150))).scalars().all()

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
        table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; }
        th, td { padding: 11px; text-align: left; border-bottom: 1px solid #334155; font-size: 13px; }
        th { background: #0f172a; }
        input, textarea { width: 100%; padding: 8px; margin-top: 5px; margin-bottom: 10px; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 6px; }
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
        <div class="tab active" onclick="showTab('tab-agents', this)">1. Жизнь Улья (Королева + Разведчицы + Фуражиры + Приёмщицы + Стражи)</div>
        <div class="tab" onclick="showTab('tab-meta', this)">2. Выявленные Зависимости & RAG-мост Гермеса</div>
        <div class="tab" onclick="showTab('tab-memory', this)">3. Соты Памяти (Базовый замер vs Ускорение)</div>
        <div class="tab" onclick="showTab('tab-resources', this)">4. Поле 120 URL & Локальная модель</div>
        <div class="tab" onclick="showTab('tab-models', this)">5. Роли и Промпты</div>
        <div class="tab" onclick="showTab('tab-logs', this)">6. Логи действий пользователей</div>
    </div>

    <div id="tab-agents" class="tab-content">
        <table>
            <thead><tr><th>Пчела</th><th>Каста в Улье</th><th>Статус</th><th>Текущий узел / Ресурс</th><th>Выполняемая операция</th><th>Обработано</th></tr></thead>
            <tbody id="agents-tbody"></tbody>
        </table>
    </div>

    <div id="tab-meta" class="tab-content" style="display:none;">
        <div class="card" style="border-left-color:#3b82f6; margin-bottom:16px;">
            <b>🔗 Внешний RAG-шлюз для навыка Гермеса (Telegram) активен на порту :8000:</b><br>
            <small style="color:#94a3b8;">
                &bull; Полный дайджест по слеш-команде: <code>GET /api/hermes/digest?limit=10&only_accelerating=false</code><br>
                &bull; Контекстный векторный поиск (pgvector): <code>GET /api/hermes/search?q=ваш_запрос&limit=7</code><br>
                &bull; Запись вывода от Гермеса обратно в Улей: <code>POST /api/hermes/ingest-insight</code>
            </small>
        </div>
        <div id="meta-container"></div>
    </div>

    <div id="tab-memory" class="tab-content" style="display:none;">
        <div style="margin-bottom:12px; display:flex; justify-content:space-between; align-items:center;">
            <span><b>Математика Пчёл-Приёмщиц:</b> 1-е упоминание = <code>ЗАМЕР БАЗЫ (0.0x)</code>. Ускорение растет только при превышении базовой частоты в новых окнах.</span>
            <button class="btn btn-red" onclick="clearMemory()">🧹 Очистить соты памяти</button>
        </div>
        <div id="trends-container"></div>
    </div>

    <div id="tab-resources" class="tab-content" style="display:none;">
        <div id="creds-container"></div>
        <h3>🌺 Поле ресурсов (<span id="res-count">0</span> URL — равномерный облет сверху донизу):</h3>
        <table>
            <thead><tr><th>Температура</th><th>Ресурс и URL</th><th>Теги и Гео</th><th>Коннектор</th><th>Здоровье</th><th>Кто на цветке</th><th>Статус</th></tr></thead>
            <tbody id="resources-tbody"></tbody>
        </table>
    </div>

    <div id="tab-models" class="tab-content" style="display:none;"><div id="models-container"></div></div>

    <div id="tab-logs" class="tab-content" style="display:none;">
        <h3>📋 Журнал действий пользователей и внешних запросов Гермеса (Audit Log):</h3>
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
    }
    async function loadMetaInsights() {
        const items = await (await fetch('/api/memory/meta-insights')).json();
        document.getElementById('meta-container').innerHTML = items.length ? items.map(m => `
            <div class="card" style="border-left-color:#10b981;">
                <b>💡 ${m.title}</b> <span class="badge">Связанные соты: ${(m.linked_trend_codes || []).join(', ')}</span>
                <p style="white-space:pre-line;"><b>Пересечение сигналов:</b>\n${m.consumer_pain_summary}</p>
                <p style="color:#34d399;"><b>Вывод / Направление:</b> ${m.investment_or_product_thesis}</p>
            </div>
        `).join('') : '<p>Пчела-Приёмщица выводит новые кросс-зависимости по мере накопления пересекающихся сигналов...</p>';
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
            <td>${r.locked_by_agent || '-'}</td><td><button class="btn ${r.is_active ? 'btn-green' : 'btn-red'}" onclick="toggleRes(${r.id})">${r.is_active ? 'Вкл' : 'Выкл'}</button></td></tr>
        `).join('');
    }
    async function saveCred(service_name, title) {
        const val = document.getElementById('cred-' + service_name).value;
        await fetch('/api/credentials', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({service_name, title, api_key_or_token: val})});
        alert('Сохранено в БД и записано в Лог действий!');
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

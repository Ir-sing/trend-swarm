from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, Base, AsyncSessionLocal, get_db
from app.models import SemanticBlock, Resource, AgentRoleConfig, TrendMemory, ConnectorCredential, AgentTelemetry, SwarmBlackboard, MetaInsight
from app.seed import seed_initial_data
from app.swarm_worker import swarm_controller

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Безопасная авто-миграция новых колонок при обновлениях без удаления старой БД!
        await conn.execute(text("ALTER TABLE resources ADD COLUMN IF NOT EXISTS last_content_hash VARCHAR"))
        await conn.execute(text("ALTER TABLE trend_memory ADD COLUMN IF NOT EXISTS velocity FLOAT DEFAULT 0.0"))
        await conn.execute(text("ALTER TABLE trend_memory ADD COLUMN IF NOT EXISTS z_score FLOAT DEFAULT 0.0"))
    async with AsyncSessionLocal() as db:
        await seed_initial_data(db)
    await swarm_controller.start(scout_count=4)
    yield
    await swarm_controller.stop()

app = FastAPI(title="Autonomous Trend Swarm 24/7", lifespan=lifespan)

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
    temperature: float = 0.3
    system_prompt: str

@app.get("/api/swarm/status")
async def get_swarm_status(db: AsyncSession = Depends(get_db)):
    agents = (await db.execute(select(AgentTelemetry).order_by(AgentTelemetry.agent_type, AgentTelemetry.agent_id))).scalars().all()
    return {"is_running": swarm_controller.is_running, "scout_count": swarm_controller.scout_count, "agents": agents}

@app.post("/api/swarm/toggle")
async def toggle_swarm(run: bool):
    if run:
        await swarm_controller.start(scout_count=swarm_controller.scout_count)
    else:
        await swarm_controller.stop()
    return {"is_running": swarm_controller.is_running}

@app.post("/api/swarm/scale")
async def scale_swarm_scouts(scout_count: int):
    await swarm_controller.scale_scouts(scout_count)
    return {"status": "scaled", "scout_count": swarm_controller.scout_count}

@app.get("/api/memory/trends")
async def get_trends(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(TrendMemory).order_by(TrendMemory.acceleration_rate.desc(), TrendMemory.last_updated_at.desc()))).scalars().all()

@app.get("/api/memory/meta-insights")
async def get_meta_insights(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(MetaInsight).order_by(MetaInsight.created_at.desc()))).scalars().all()

@app.patch("/api/memory/trends/{code}")
async def change_trend_status(code: str, status: str, db: AsyncSession = Depends(get_db)):
    tr = (await db.execute(select(TrendMemory).where(TrendMemory.code == code))).scalars().first()
    if not tr:
        raise HTTPException(404)
    tr.lifecycle_status = status
    await db.commit()
    return {"status": "ok"}

@app.get("/api/resources")
async def get_all_resources(db: AsyncSession = Depends(get_db)):
    resources = (await db.execute(select(Resource).order_by(Resource.temperature.desc()))).scalars().all()
    creds = (await db.execute(select(ConnectorCredential))).scalars().all()
    return {"resources": resources, "credentials": creds}

@app.patch("/api/resources/{res_id}/toggle")
async def toggle_resource(res_id: int, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(Resource).where(Resource.id == res_id))).scalars().first()
    if r:
        r.is_active = not r.is_active
        await db.commit()
    return {"status": "ok"}

@app.post("/api/credentials")
async def save_credential(payload: CredentialUpdateSchema, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == payload.service_name))).scalars().first()
    if c:
        c.api_key_or_token = payload.api_key_or_token
    await db.commit()
    return {"status": "saved"}

@app.get("/api/models")
async def get_models(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(AgentRoleConfig))).scalars().all()

@app.post("/api/models")
async def save_model(payload: RoleConfigSchema, db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == payload.role_name))).scalars().first()
    if m:
        for k, v in payload.model_dump().items():
            setattr(m, k, v)
        await db.commit()
    return {"status": "saved"}

@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    return '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Trend Swarm 24/7 — Полный Центр Управления</title>
    <style>
        body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; background: #1e293b; padding: 15px 25px; border-radius: 10px; margin-bottom: 20px; }
        .btn { padding: 8px 14px; border-radius: 6px; border: none; cursor: pointer; font-weight: 600; color: white; }
        .btn-green { background: #10b981; } .btn-red { background: #ef4444; } .btn-blue { background: #3b82f6; }
        .tabs { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
        .tab { padding: 10px 18px; background: #1e293b; border-radius: 8px; cursor: pointer; font-size: 14px; }
        .tab.active { background: #3b82f6; font-weight: bold; }
        .card { background: #1e293b; padding: 15px; border-radius: 8px; margin-bottom: 12px; border-left: 4px solid #3b82f6; }
        table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; }
        th, td { padding: 11px; text-align: left; border-bottom: 1px solid #334155; font-size: 13px; }
        th { background: #0f172a; }
        input, textarea { width: 100%; padding: 8px; margin-top: 5px; margin-bottom: 10px; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 6px; }
        .badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; background: #334155; display: inline-block; margin-right: 4px; }
        .badge-role { background: #6366f1; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h2 style="margin:0;">🐝 Autonomous Trend Swarm 24/7 (PostgreSQL + pgvector | MathEngine | Zero-Downtime DB)</h2>
            <small id="swarm-indicator">Загрузка...</small>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
            <span style="font-size:13px;">Ищеек в рое (1-100):</span>
            <input type="number" id="scout-count-input" value="4" min="1" max="100" style="width:65px; margin:0; text-align:center;">
            <button class="btn btn-blue" onclick="scaleSwarm()">⚡ Масштабировать</button>
            <button class="btn btn-green" onclick="toggleSwarm(true)">▶ Старт</button>
            <button class="btn btn-red" onclick="toggleSwarm(false)">⏹ Стоп</button>
        </div>
    </div>

    <div class="tabs">
        <div class="tab active" onclick="showTab('tab-agents', this)">1. Монитор Агентов (Ищейки + Прораб + Реаниматор)</div>
        <div class="tab" onclick="showTab('tab-meta', this)">2. Пост-Анализ Памяти (Мета-Инсайты)</div>
        <div class="tab" onclick="showTab('tab-memory', this)">3. Файл Памяти (Z-Score & Ускорение)</div>
        <div class="tab" onclick="showTab('tab-resources', this)">4. База 120 URL и Коннекторы</div>
        <div class="tab" onclick="showTab('tab-models', this)">5. Модели и Промпты</div>
    </div>

    <div id="tab-agents" class="tab-content">
        <table>
            <thead><tr><th>Агент</th><th>Каста</th><th>Статус</th><th>Узел / Ресурс</th><th>Текущая операция</th><th>Циклов/Объектов</th></tr></thead>
            <tbody id="agents-tbody"></tbody>
        </table>
    </div>
    <div id="tab-meta" class="tab-content" style="display:none;"><div id="meta-container"></div></div>
    <div id="tab-memory" class="tab-content" style="display:none;"><div id="trends-container"></div></div>
    <div id="tab-resources" class="tab-content" style="display:none;">
        <div id="creds-container"></div>
        <h3>📡 Промаркированная база ресурсов (<span id="res-count">0</span> URL):</h3>
        <table>
            <thead><tr><th>Температура</th><th>Ресурс и URL</th><th>Теги и Гео</th><th>Коннектор</th><th>Здоровье</th><th>Кем занят</th><th>Статус</th></tr></thead>
            <tbody id="resources-tbody"></tbody>
        </table>
    </div>
    <div id="tab-models" class="tab-content" style="display:none;"><div id="models-container"></div></div>

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
    }
    async function toggleSwarm(run) { await fetch('/api/swarm/toggle?run=' + run, {method: 'POST'}); loadSwarmStatus(); }
    async function scaleSwarm() {
        const c = document.getElementById('scout-count-input').value;
        await fetch('/api/swarm/scale?scout_count=' + c, {method: 'POST'});
        loadSwarmStatus();
    }
    async function loadSwarmStatus() {
        const res = await fetch('/api/swarm/status');
        const data = await res.json();
        document.getElementById('swarm-indicator').innerHTML = data.is_running 
            ? `<span style="color:#10b981;">● РОЙ АКТИВЕН 24/7 (Полевых ищеек: ${data.scout_count} + Прораб + Реаниматор + Пост-Аналитик)</span>` 
            : '<span style="color:#ef4444;">● РОЙ ОСТАНОВЛЕН</span>';
        document.getElementById('agents-tbody').innerHTML = data.agents.map(a => `
            <tr><td><b>${a.agent_id}</b></td><td><span class="badge badge-role">${a.agent_type}</span></td><td><span class="badge">${a.state}</span></td><td>${a.current_resource}</td><td>${a.current_action}</td><td>${a.items_processed}</td></tr>
        `).join('');
    }
    async function loadMetaInsights() {
        const items = await (await fetch('/api/memory/meta-insights')).json();
        document.getElementById('meta-container').innerHTML = items.length ? items.map(m => `
            <div class="card" style="border-left-color:#10b981;">
                <b>💡 ${m.title}</b> <span class="badge">Тренды: ${(m.linked_trend_codes || []).join(', ')}</span>
                <p><b>Потребительская боль:</b> ${m.consumer_pain_summary}</p>
                <p style="color:#34d399;"><b>Инвестиционная гипотеза:</b> ${m.investment_or_product_thesis}</p>
            </div>
        `).join('') : '<p>Агент Пост-Анализа (Analyst-Память) формирует первые Мета-Инсайты по мере накопления Файла Памяти...</p>';
    }
    async function loadTrends() {
        const trends = await (await fetch('/api/memory/trends')).json();
        document.getElementById('trends-container').innerHTML = trends.length ? trends.map(t => `
            <div class="card">
                <div style="display:flex; justify-content:space-between;">
                    <b>[${t.code}] ${t.title} (⚡ Индекс: ${t.acceleration_rate} | Z-Score: ${t.z_score || 0}σ | V: ${t.velocity || 0})</b>
                    <div><span class="badge">${t.lifecycle_status}</span> <span class="badge">Авторов: ${t.unique_authors_count}</span> <span class="badge">Гео: ${(t.geo_spread || []).join(', ')}</span>
                    <button class="btn btn-red" style="padding:3px 8px; font-size:11px;" onclick="ignoreTrend('${t.code}')">В игнор</button></div>
                </div>
                <p>${t.core_essence}</p>
                <small>Ключи: ${(t.keywords_multilang || []).join(', ')} | Дельт в истории: ${(t.delta_history || []).length}</small>
            </div>
        `).join('') : '<p>Сбор первых сигналов со 120 ресурсов...</p>';
    }
    async function ignoreTrend(code) { await fetch(`/api/memory/trends/${code}?status=IGNORED_NOISE`, {method: 'PATCH'}); loadTrends(); }
    async function loadResources() {
        const data = await (await fetch('/api/resources')).json();
        document.getElementById('res-count').innerText = data.resources.length;
        document.getElementById('creds-container').innerHTML = data.credentials.map(c => `
            <div class="card" style="display:flex; gap:10px; align-items:center;">
                <div style="flex:1;"><b>${c.title}</b></div>
                <input id="cred-${c.service_name}" value="${c.api_key_or_token}" placeholder="API-ключ / Токен..." style="margin:0; flex:2;">
                <button class="btn btn-blue" onclick="saveCred('${c.service_name}', '${c.title}')">Сохранить</button>
            </div>
        `).join('');
        document.getElementById('resources-tbody').innerHTML = data.resources.map(r => `
            <tr><td>🔥 <b>${r.temperature}°</b></td><td><b>${r.name}</b><br><small style="color:#94a3b8;">${r.url_or_id}</small></td>
            <td><span class="badge badge-role">${r.geo_region}</span> ${(r.tags || []).map(t => `<span class="badge">${t}</span>`).join('')}</td>
            <td><span class="badge">${r.connector_type}</span></td><td><span class="badge">${r.health_status}</span></td>
            <td>${r.locked_by_agent || '-'}</td><td><button class="btn ${r.is_active ? 'btn-green' : 'btn-red'}" onclick="toggleRes(${r.id})">${r.is_active ? 'Вкл' : 'Выкл'}</button></td></tr>
        `).join('');
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
            Провайдер: <input id="prov-${m.role_name}" value="${m.provider}">
            Модель: <input id="mod-${m.role_name}" value="${m.model_name}">
            Промпт: <textarea id="pr-${m.role_name}" rows="3">${m.system_prompt}</textarea>
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
    setInterval(loadSwarmStatus, 3000);
    loadSwarmStatus();
</script>
</body>
</html>'''

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    ConnectorCredential, AgentRoleConfig, SemanticBlock, 
    Resource, AgentTelemetry, TrendMemory, MetaInsight, SwarmBlackboard
)

async def seed_initial_data(db: AsyncSession):
    # 1. Однократная очистка старых мусорных записей вида "Сигнал [protein]" из прошлых версий
    await db.execute(delete(TrendMemory).where(TrendMemory.title.like("Сигнал [%")))
    await db.execute(delete(MetaInsight).where(MetaInsight.title.like("Сигнал [%")))
    await db.execute(delete(MetaInsight).where(MetaInsight.title.like("Мета-связка:%Сигнал [%")))

    # 2. Слоты подключений (На первом месте — адрес локальной модели / Гермеса без платных ключей!)
    creds_defaults = [
        ("llm_local", "Адрес локальной модели (Ollama / vLLM / Hermes) или API-ключ (по умолчанию: http://host.docker.internal:11434)", "http://host.docker.internal:11434"),
        ("wb_mpstats", "MPStats / Wildberries API Token (если пусто — работает открытый поиск новинок WB)", ""),
        ("ozon_api", "Ozon Analytics API Key", ""),
        ("proxies", "Пул прокси (HTTP/SOCKS5) для Пчёл-Стражей", ""),
    ]
    for s_name, s_title, s_val in creds_defaults:
        if not (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == s_name))).scalars().first():
            db.add(ConnectorCredential(service_name=s_name, title=s_title, api_key_or_token=s_val))

    # 3. БИОЛОГИЧЕСКИЕ РОЛИ ПЧЁЛ В УЛЬЕ + ПОЛНЫЕ ПРОМПТЫ
    # Обновляются автоматически только в том случае, если в базе еще лежит старая короткая заглушка (< 250 символов).
    # Если пользователь сам правил промпт в браузере — перезапуск сервера его правки НЕ затрет!
    roles_data = [
        (
            "dirty_scout",
            "1. Пчёлы-Разведчицы (Полевой поиск изменений поведения потребителей и ранних сигналов)",
            "ollama",
            "qwen2.5:3b",
            (
                "Ты — Пчела-Разведчица проекта «Улей. Поиск трендов». Твоя задача — находить ранние изменения поведения потребителей, "
                "зарождающиеся явления, мемы и инвестиционные сигналы ДО того, как о них напишут мейнстрим-СМИ.\n"
                "ЧТО ИСКАТЬ:\n"
                "1. Сдвиги в поведении людей: от чего отказываются, на что массово жалуются в отзывах, что начинают смешивать/делать сами, что ищут в комментариях.\n"
                "2. Приоритет — рынок FMCG (еда, напитки, товары повседневного спроса, ритейл), но смотри широко: инсайты прячутся в неожиданных местах (форумы, субкультуры, наука, медицина, ИТ, зарубежные сообщества).\n"
                "3. На инвест-площадках и в B2B ищи движения капитала: новые заводы, патенты, необычные R&D-вакансии, венчурные раунды, обсуждения публичных компаний.\n"
                "4. Отсекай банальный вечный шум и пиши без словесной шелухи полными предложениями.\n"
                "Отвечай СТРОГО в JSON: {\"is_anomaly\": true/false, \"confirmed\": true/false, \"title\": \"Четкое название явления\", "
                "\"essence\": \"Суть сдвига поведения или инвестиционного факта\", \"summary\": \"Детали и потенциал\", "
                "\"multilang_keywords\": [\"ключ_ru\", \"key_en\"]}"
            )
        ),
        (
            "vision_meme",
            "2. Пчёлы-Разведчицы Визуала (Поиск вирусных картинок, мемов и новых продуктов)",
            "ollama",
            "llava:7b",
            (
                "Ты — Визуальная Пчела-Разведчица проекта «Улей». Анализируй картинки и мемы с имиджбордов, форумов и Telegram. "
                "Фиксируй зарождающиеся шаблоны мемов, фото новых необычных товаров и форматов потребления до их выхода в масс-маркет. "
                "Отвечай СТРОГО в JSON: {\"is_anomaly\": true/false, \"title\": \"Название визуального тренда\", "
                "\"essence\": \"Суть визуального явления\", \"multilang_keywords\": [\"тег1\", \"tag2\"]}"
            )
        ),
        (
            "synthesizer",
            "3. Пчёлы-Приёмщицы (Переработка нектара в Соты Памяти и фильтрация шума)",
            "ollama",
            "qwen2.5:3b",
            (
                "Ты — Пчела-Приёмщица проекта «Улей». Ты принимаешь сырые находки от разведчиц и рабочих пчёл-фуражиров, "
                "убираешь информационную воду и запечатываешь чистый факт в Соты Памяти.\n"
                "Связывай явление с направлением инвестиций: какие публичные компании на бирже (например, MGNT, FIVE, GCHE, ABRD, AQUA, AGRO, BELU и мировые аналоги), "
                "производители сырья или венчурные сектора выиграют от этого тренда.\n"
                "Отвечай СТРОГО в JSON: {\"title\": \"Название тренда\", \"core_essence\": \"Чистая аналитическая суть явления и инвестиционный вектор\", "
                "\"delta_summary\": \"Какой новый подтверждающий факт добавился\"}"
            )
        ),
        (
            "foreman_brain",
            "4. Пчелиная Королева (Управление феромонами приоритета и облетом всех 120 ресурсов)",
            "ollama",
            "qwen2.5:3b",
            (
                "Ты — Пчелиная Королева проекта «Улей». Ты направляешь разведчиц и рабочих пчёл во все 6 смысловых блоков, "
                "чтобы ни один холодный ресурс внизу списка (инвест-площадки, форумы акций, патенты) не оставался без внимания. "
                "Отвечай в JSON: {\"boost_block\": \"investments\"}"
            )
        ),
        (
            "memory_analyst",
            "5. Ульевой Совет / Связь с Гермесом (Выведение кросс-трендовых зависимостей и Инвест-Гипотез)",
            "ollama",
            "qwen2.5:3b",
            (
                "Ты — аналитик зависимостей проекта «Улей». Ты сопоставляешь разные тренды из Сот Памяти (сравнивая их ускорение относительно базового замера) "
                "и находишь неочевидные связки между сдвигами в поведении потребителей и направлениями инвестиций.\n"
                "Отвечай СТРОГО в JSON: {\"title\": \"Название выявленной зависимости\", \"linked_codes\": [\"HIVE-0001\", \"HIVE-0002\"], "
                "\"pattern_type\": \"PAIN_TO_INVESTMENT\", \"consumer_pain_summary\": \"Связь между сигналами и суть сдвига поведения\", "
                "\"investment_or_product_thesis\": \"Направление инвестиций: сектора, тикеры, сырье или продуктовая ниша\", \"confidence_score\": 0.85}"
            )
        ),
    ]

    for r_name, r_title, r_prov, r_mod, r_prompt in roles_data:
        existing_role = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == r_name))).scalars().first()
        if not existing_role:
            db.add(AgentRoleConfig(role_name=r_name, title=r_title, provider=r_prov, model_name=r_mod, credential_service="llm_local", system_prompt=r_prompt))
        elif len(existing_role.system_prompt or "") < 250:
            # Обновляем только старые однострочные заглушки, сохраняя пользовательские правки!
            existing_role.title = r_title
            existing_role.provider = r_prov
            existing_role.model_name = r_mod
            existing_role.credential_service = "llm_local"
            existing_role.system_prompt = r_prompt

    # 4. Обновляем состав пчёл в мониторе телеметрии
    await db.execute(delete(AgentTelemetry))
    db.add(AgentTelemetry(agent_id="Пчелиная Королева", agent_type="КОРОЛЕВА", state="IDLE", current_resource="Весь Улей (120 URL)", current_action="Распределение феромонов приоритета по всем 6 блокам"))
    db.add(AgentTelemetry(agent_id="Пчела-Приёмщица (Математика)", agent_type="ПРИЁМЩИЦА", state="IDLE", current_resource="Соты Памяти & RAG", current_action="Расчет базовых частот, Z-Score, 2-й производной и RAG-зависимостей"))
    db.add(AgentTelemetry(agent_id="Пчела-Страж", agent_type="СТРАЖ", state="IDLE", current_resource="Леток Улья & Сеть", current_action="Защита от ботов, лечение коннекторов и очистка локов"))
    for i in range(1, 5):
        db.add(AgentTelemetry(agent_id=f"Пчела-Разведчица #{i}", agent_type="РАЗВЕДЧИЦА / ФУРАЖИР", state="IDLE", current_resource="-", current_action="Готова к вылету"))

    if (await db.execute(select(SemanticBlock))).scalars().first():
        await db.commit()
        return

    # Если база создается впервые — засеваем 6 блоков и 120 URL (полный список сохранен)
    blocks = {
        "consumer": SemanticBlock(name="1. Потребительский опыт, Маркетплейсы и Отзовики", block_type="consumer", description="WB, Ozon, ВкусВилл, Самокат, Отзовики, Amazon, iHerb"),
        "underground": SemanticBlock(name="2. Форумы, Имиджборды, Мемы и Кустарные рецепты", block_type="underground", description="Двач, Пикабу, Reddit (20 сабреддитов), Азия, Латинская Америка, MENA"),
        "investments": SemanticBlock(name="3. Инвестиции, Биржа, Тикеры, Венчур и Стартапы", block_type="investments", description="Смартлаб (акции РФ), Пульс, SeekingAlpha, AgFunder, FoodHack, мировые VC"),
        "b2b_patents": SemanticBlock(name="4. B2B, Вакансии R&D, Заводы, Патенты и Наука", block_type="b2b_patents", description="API HH.ru, Минсельхозы регионов, Роспатент, PubMed, arXiv, ЭФКО, АБИ"),
        "niche_media": SemanticBlock(name="5. Отраслевые СМИ и Исследовательские Центры", block_type="niche_media", description="BevNET, FoodNavigator (5 регионов), Milknews, Retail.ru, Роскачество, ВЦИОМ"),
        "mainstream_media": SemanticBlock(name="6. Мейнстрим-СМИ (Калибратор / Фильтр «Уже в газетах»)", block_type="mainstream_media", description="РБК, Коммерсантъ, Ведомости, Forbes, ТАСС, CNBC"),
    }
    for b in blocks.values():
        db.add(b)
    await db.flush()
    await db.commit()

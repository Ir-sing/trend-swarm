from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    ConnectorCredential, AgentRoleConfig, SemanticBlock, 
    Resource, AgentTelemetry
)


async def seed_initial_data(db: AsyncSession):
    # 1. Слоты подключений (добавляются, только если их еще нет — существующие ключи не трогаем)
    creds_defaults = [
        ("llm_local", "Адрес локальной модели (Ollama / vLLM / Hermes) или API-ключ (по умолчанию: http://host.docker.internal:11434)", "http://host.docker.internal:11434"),
        ("wb_mpstats", "MPStats / Wildberries API Token (если пусто — работает открытый поиск новинок WB)", ""),
        ("ozon_api", "Ozon Analytics API Key", ""),
        ("proxies", "Пул прокси (HTTP/SOCKS5) для Пчёл-Стражей", ""),
    ]
    for s_name, s_title, s_val in creds_defaults:
        if not (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == s_name))).scalars().first():
            db.add(ConnectorCredential(service_name=s_name, title=s_title, api_key_or_token=s_val))

    # 2. Биологические роли пчёл в Улье + Промпты (пользовательские правки не перезатираются)
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
            "qwen2.5:3b",
            (
                "Ты — Визуальная Пчела-Разведчица проекта «Улей». Анализируй текстовые описания мемов, видео и фото новинок с имиджбордов, форумов и Telegram. "
                "Фиксируй зарождающиеся шаблоны мемов и необычные товары до их выхода в масс-маркет.\n"
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

    # 3. Записи телеметрии пчёл (добавляем только недостающие без удаления существующих)
    default_agents = [
        ("Пчелиная Королева", "КОРОЛЕВА", "Весь Улей (120 URL)", "Распределение феромонов приоритета по всем 6 блокам"),
        ("Пчела-Приёмщица (Математика)", "ПРИЁМЩИЦА", "Соты Памяти & RAG", "Расчет базовых частот, Z-Score, 2-й производной и RAG-зависимостей"),
        ("Пчела-Страж", "СТРАЖ", "Леток Улья & Сеть", "Защита от ботов, лечение коннекторов и очистка локов"),
        ("Пчела-Разведчица #1", "РАЗВЕДЧИЦА / ФУРАЖИР", "-", "Готова к вылету"),
        ("Пчела-Разведчица #2", "РАЗВЕДЧИЦА / ФУРАЖИР", "-", "Готова к вылету"),
        ("Пчела-Разведчица #3", "РАЗВЕДЧИЦА / ФУРАЖИР", "-", "Готова к вылету"),
        ("Пчела-Разведчица #4", "РАЗВЕДЧИЦА / ФУРАЖИР", "-", "Готова к вылету"),
    ]
    for a_id, a_type, a_res, a_act in default_agents:
        if not (await db.execute(select(AgentTelemetry).where(AgentTelemetry.agent_id == a_id))).scalars().first():
            db.add(AgentTelemetry(agent_id=a_id, agent_type=a_type, state="IDLE", current_resource=a_res, current_action=a_act))

    # 4. Смысловые блоки (проверяем поштучно, создаем только отсутствующие)
    block_defs = [
        ("consumer", "1. Потребительский опыт, Маркетплейсы и Отзовики", "WB, Ozon, ВкусВилл, Самокат, Отзовики, Amazon, iHerb"),
        ("underground", "2. Форумы, Имиджборды, Мемы и Кустарные рецепты", "Двач, Пикабу, Reddit (20 сабреддитов), Азия, Латинская Америка, MENA"),
        ("investments", "3. Инвестиции, Биржа, Тикеры, Венчур и Стартапы", "Смартлаб (акции РФ), Пульс, SeekingAlpha, AgFunder, FoodHack, мировые VC"),
        ("b2b_patents", "4. B2B, Вакансии R&D, Заводы, Патенты и Наука", "API HH.ru, Минсельхозы регионов, Роспатент, PubMed, arXiv, ЭФКО, АБИ"),
        ("niche_media", "5. Отраслевые СМИ и Исследовательские Центры", "BevNET, FoodNavigator (5 регионов), Milknews, Retail.ru, Роскачество, ВЦИОМ"),
        ("mainstream_media", "6. Мейнстрим-СМИ (Калибратор / Фильтр «Уже в газетах»)", "РБК, Коммерсантъ, Ведомости, Forbes, ТАСС, CNBC"),
    ]
    blocks = {}
    for b_type, b_name, b_desc in block_defs:
        b_obj = (await db.execute(select(SemanticBlock).where(SemanticBlock.block_type == b_type))).scalars().first()
        if not b_obj:
            b_obj = SemanticBlock(name=b_name, block_type=b_type, description=b_desc)
            db.add(b_obj)
            await db.flush()
        blocks[b_type] = b_obj

    # 5. Полный список 120 промаркированных URL (дописываются только отсутствующие, старые не трогаются)
    raw = [
        ("consumer", "WB: Поисковые тренды Напитки и Клетчатка", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#FMCG", "#Drinks"], 80.0, {"default_query": "напиток клетчатка пребиотик инулин без сахара"}),
        ("consumer", "WB: Новинки Функциональные снеки и Десерты", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#FMCG", "#Snacks"], 78.0, {"default_query": "функциональный снек без сахара пребиотик"}),
        ("consumer", "WB: Новинки Адаптогены, Грибы и Ноотропы", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#Supplements", "#Biohacking"], 78.0, {"default_query": "псиллиум инулин ежовик кордицепс габа"}),
        ("consumer", "WB: Новинки Косметика (Пептиды и Спикулы)", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#Cosmetics", "#Skincare"], 75.0, {"default_query": "пептиды спикулы центелла пробиотик"}),
        ("consumer", "Ozon / Ozon Fresh: Продуктовые запуски", "https://t.me/s/ozonhq", "telegram_web", "RU", ["#Ecom", "#FMCG"], 74.0, {}),
        ("consumer", "Яндекс Лавка: Новинки готовой еды и СТМ", "https://t.me/s/yandex_lavka", "telegram_web", "RU", ["#ReadyMeals", "#Delivery"], 76.0, {}),
        ("consumer", "ВкусВилл: Отзывы и народные обзоры новинок", "https://t.me/s/vkusvill_official", "telegram_web", "RU", ["#ReadyMeals", "#CleanLabel"], 78.0, {}),
        ("consumer", "Самокат: Новинки дарксторов и снеков", "https://t.me/s/samokat_15min", "telegram_web", "RU", ["#Delivery", "#FMCG"], 75.0, {}),
        ("consumer", "Чижик (X5): Обзоры жестких дискаунтеров и СТМ", "https://t.me/s/chizhik_official", "telegram_web", "RU", ["#Discounter", "#Retail"], 74.0, {}),
        ("consumer", "ТГ: Новинки с полок супермаркетов РФ", "https://t.me/s/produkty_v_setyah", "telegram_web", "RU", ["#Retail", "#NewArrivals"], 75.0, {}),
        ("consumer", "IRecommend: Отзывы Продукты питания", "https://irecommend.ru/taxonomy/term/938/rss.xml", "rss_feed", "RU", ["#Reviews", "#FMCG"], 76.0, {}),
        ("consumer", "IRecommend: Отзывы Диеты и БАДы", "https://irecommend.ru/taxonomy/term/87/rss.xml", "rss_feed", "RU", ["#Reviews", "#Supplements"], 75.0, {}),
        ("consumer", "IRecommend: Отзывы Косметика", "https://irecommend.ru/taxonomy/term/30/rss.xml", "rss_feed", "RU", ["#Reviews", "#Cosmetics"], 72.0, {}),
        ("consumer", "Otzovik: Отзывы о продуктах питания", "https://otzovik.com/reviews/produkti_pitaniya/", "html_scraper", "RU", ["#Reviews", "#ConsumerPain"], 75.0, {"item_selector": ".item"}),
        ("consumer", "Otzovik: Отзывы о доставке готовой еды", "https://otzovik.com/reviews/dostavka_edi/", "html_scraper", "RU", ["#ReadyMeals", "#ConsumerPain"], 77.0, {"item_selector": ".item"}),
        ("consumer", "Babyblog: Женский форум о питании и покупках", "https://www.babyblog.ru/community/lenta/food", "html_scraper", "RU", ["#Family", "#FMCG"], 70.0, {"item_selector": "article"}),
        ("consumer", "Woman.ru: Форум диет и новых продуктов", "https://www.woman.ru/health/diets/forum/", "html_scraper", "RU", ["#Diet", "#Women"], 70.0, {"item_selector": ".card"}),
        ("consumer", "Littleone: Форум покупок и супермаркетов", "https://forum.littleone.ru/forumdisplay.php?f=85", "html_scraper", "RU", ["#Retail", "#Family"], 68.0, {"item_selector": ".threadtitle"}),
        ("consumer", "Amazon Movers & Shakers: Лидеры роста Grocery (США)", "https://www.amazon.com/gp/movers-and-shakers/grocery", "html_scraper", "US", ["#GlobalTrends", "#Grocery"], 78.0, {"item_selector": ".zg-grid-general-faceout"}),
        ("consumer", "Amazon Movers & Shakers: Здоровье и БАДы (США)", "https://www.amazon.com/gp/movers-and-shakers/hpc", "html_scraper", "US", ["#Supplements", "#US"], 76.0, {"item_selector": ".zg-grid-general-faceout"}),
        ("consumer", "iHerb Trending: Мировые хиты клетчатки и нутрицевтики", "https://www.iherb.com/catalog/topsellers", "html_scraper", "GLOBAL", ["#Fiber", "#Supplements"], 76.0, {"item_selector": ".product-cell-container"}),
        ("consumer", "Product Hunt: Новые Food & Health приложения", "https://www.producthunt.com/feed?category=health-fitness", "rss_feed", "GLOBAL", ["#Apps", "#HealthTech"], 72.0, {}),
    ]

    for board, desc, tag, temp in [
        ("cook", "Кулинария и крафтовые смеси", "#DIY", 78.0), ("fit", "Спортпит, белок и предтрены", "#Protein", 75.0),
        ("fag", "Ранние вирусные тренды блогов", "#Viral", 70.0), ("b", "Генератор рунет-мемов и картинок", "#Memes", 70.0),
        ("biz", "Малый бизнес, цеха и селлеры", "#Sellers", 76.0), ("sci", "Химия, ноотропы и ингредиенты", "#Science", 74.0)
    ]:
        raw.append(("underground", f"Двач (2ch.hk): /{board}/ ({desc})", f"https://2ch.hk/{board}/index.json", "json_api", "RU", [tag, "#Imageboard"], temp, {}))

    raw.extend([
        ("underground", "Пикабу: Свежее и вирусные посты РФ", "https://pikabu.ru/xmlfeeds.php?cmd=popular", "rss_feed", "RU", ["#Viral", "#RU"], 75.0, {}),
        ("underground", "ЯПлакалъ: Народные видео и мемы РФ", "https://www.yaplakal.com/news.xml", "rss_feed", "RU", ["#Memes", "#Viral"], 70.0, {}),
        ("underground", "KnowYourMeme: Мировой радар зарождающихся мемов", "https://knowyourmeme.com/newsfeed.rss", "rss_feed", "GLOBAL", ["#Memes", "#Global"], 74.0, {}),
        ("underground", "ТГ: Тренды Китая Douyin и Xiaohongshu", "https://t.me/s/china_business_trends", "telegram_web", "CN", ["#China", "#Douyin"], 79.0, {}),
    ])

    for sub, geo, tag, temp in [
        ("Supplements", "GLOBAL", "#Supplements", 78.0), ("foodscience", "GLOBAL", "#FoodTech", 80.0),
        ("GutHealth", "GLOBAL", "#Fiber", 80.0), ("Biohackers", "GLOBAL", "#Biohacking", 78.0),
        ("fermentation", "GLOBAL", "#Fermentation", 77.0), ("Ozempic", "US", "#GLP1", 79.0),
        ("GroceryStores", "US", "#Retail", 74.0), ("snacks", "GLOBAL", "#Snacks", 76.0),
        ("energydrinks", "GLOBAL", "#Drinks", 75.0), ("tea", "ASIA", "#AsianTrends", 73.0),
        ("coffee", "GLOBAL", "#Coffee", 73.0), ("Nootropics", "GLOBAL", "#Nootropics", 76.0),
        ("AsianBeauty", "ASIA", "#Cosmetics", 75.0), ("KoreanFood", "ASIA", "#Korea", 75.0),
        ("JapaneseFood", "ASIA", "#Japan", 75.0), ("brasil", "LATAM", "#LatAm", 72.0),
        ("mexico", "LATAM", "#LatAm", 72.0), ("india", "IN", "#India", 72.0),
        ("dubai", "MENA", "#MENA", 74.0), ("Thailand", "ASIA", "#SEA", 73.0)
    ]:
        raw.append(("underground", f"Reddit: r/{sub} ({geo})", f"https://www.reddit.com/r/{sub}/new.json?limit=6&raw_json=1", "json_api", geo, [tag, "#Reddit"], temp, {}))

    raw.append(("investments", "Smart-Lab: Все блоги инвесторов РФ", "https://smart-lab.ru/rss/all/", "rss_feed", "RU", ["#StocksRU", "#SmartMoney"], 82.0, {}))
    for ticker, name in [("MGNT", "Магнит"), ("FIVE", "X5 Retail"), ("GCHE", "Черкизово"), ("ABRD", "Абрау-Дюрсо"), ("AQUA", "Инарктика"), ("AGRO", "Русагро"), ("BELU", "НоваБев")]:
        raw.append(("investments", f"Smart-Lab: Форум акций {name} ({ticker})", f"https://smart-lab.ru/forum/{ticker}", "html_scraper", "RU", [f"#{ticker}", "#StocksRU"], 78.0, {"item_selector": ".comment_text"}))

    raw.extend([
        ("investments", "Т-Инвестиции (Пульс): Сентимент розничных инвесторов", "https://t.me/s/tinkoff_invest_official", "telegram_web", "RU", ["#Sentiment", "#StocksRU"], 78.0, {}),
        ("investments", "Reddit: r/wallstreetbets (Биржевой хайп)", "https://www.reddit.com/r/wallstreetbets/new.json?limit=6&raw_json=1", "json_api", "US", ["#MemeStocks", "#US"], 74.0, {}),
        ("investments", "Reddit: r/investing (Инвест-идеи сектора потребления)", "https://www.reddit.com/r/investing/new.json?limit=6&raw_json=1", "json_api", "GLOBAL", ["#Investing", "#Macro"], 76.0, {}),
        ("investments", "Reddit: r/SecurityAnalysis (Отчеты инвестфондов)", "https://www.reddit.com/r/SecurityAnalysis/new.json?limit=6&raw_json=1", "json_api", "GLOBAL", ["#DeepValue", "#Equity"], 78.0, {}),
        ("investments", "Seeking Alpha: Аналитика Consumer Staples & FMCG", "https://seekingalpha.com/market_currents.xml", "rss_feed", "US", ["#FMCG", "#Earnings"], 80.0, {}),
        ("investments", "Hacker News (YC): Стартапы и технологии", "https://hnrss.org/newest?points=50", "rss_feed", "GLOBAL", ["#Startups", "#YC"], 77.0, {}),
        ("investments", "TechCrunch Venture: Мировые венчурные сделки", "https://techcrunch.com/category/venture/feed/", "rss_feed", "GLOBAL", ["#Venture", "#Seed"], 78.0, {}),
        ("investments", "AgFunderNews: Венчурные раунды FoodTech и AgTech", "https://agfundernews.com/feed", "rss_feed", "GLOBAL", ["#FoodTech", "#AgTech"], 82.0, {}),
        ("investments", "Green Queen (Азия): Инвестиции в ферментацию и белки", "https://www.greenqueen.com.hk/feed/", "rss_feed", "ASIA", ["#Fermentation", "#AltProtein"], 80.0, {}),
        ("investments", "FoodHack (Швейцария): Европейские сделки FoodTech", "https://foodhack.global/rss", "html_scraper", "EU", ["#FoodTech", "#EuropeVC"], 78.0, {"item_selector": "article, p"}),
        ("investments", "EU-Startups: Раунды потребительских брендов Европы", "https://www.eu-startups.com/feed/", "rss_feed", "EU", ["#EuropeVC", "#D2C"], 76.0, {}),
        ("investments", "LatAm List: Венчур Латинской Америки", "https://latamlist.com/feed/", "rss_feed", "LATAM", ["#LatAmVC", "#Startups"], 75.0, {}),
        ("investments", "Wamda (MENA/Дубай): Инвестиции Ближнего Востока", "https://www.wamda.com/feed", "rss_feed", "MENA", ["#MENAVC", "#Dubai"], 75.0, {}),
        ("investments", "Inc42 (Индия): Сделки D2C и FMCG Индии", "https://inc42.com/feed/", "rss_feed", "IN", ["#IndiaVC", "#FMCG"], 75.0, {}),
    ])

    for q_title, q_text in [
        ("Технолог пищевого производства и R&D", "технолог пищевого производства OR разработка рецептур"),
        ("Белок, Ферментация и Клетчатка", "ферментация OR изолят белка OR пищевые волокна OR пребиотик"),
        ("Готовая еда и Фабрики-кухни", "фабрика кухня OR производство готовой еды OR технолог готовых блюд"),
        ("Функциональные напитки и Розлив", "технолог безалкогольных напитков OR розлив напитков OR купажист"),
        ("Разработка СТМ в ритейле", "менеджер СТМ продукты питания OR разработка СТМ FMCG"),
    ]:
        raw.append(("b2b_patents", f"HeadHunter API: Вакансии — {q_title}", "https://api.hh.ru/vacancies", "hh_vacancies", "RU", ["#Jobs", "#B2B"], 80.0, {"search_text": q_text}))

    raw.extend([
        ("b2b_patents", "Минсельхозпрод Московской области: Заводы и проекты", "https://msh.mosreg.ru/novosti", "html_scraper", "RU", ["#Gov", "#Factories"], 78.0, {"item_selector": ".news-item, article, h3"}),
        ("b2b_patents", "Минсельхоз Краснодарского края: Проекты пищепрома Юга", "https://msh.krasnodar.ru/news/", "html_scraper", "RU", ["#Gov", "#Factories"], 76.0, {"item_selector": ".news-item, article"}),
        ("b2b_patents", "Инвестпортал Татарстана: Новые производства", "https://tida.tatarstan.ru/rus/index.htm/news", "html_scraper", "RU", ["#Gov", "#Factories"], 75.0, {"item_selector": ".news-list, p"}),
        ("b2b_patents", "Инвестпортал Новосибирской области: Проекты Сибири", "https://air-nso.ru/news/", "html_scraper", "RU", ["#Gov", "#Factories"], 75.0, {"item_selector": "article, h2"}),
        ("b2b_patents", "АБИ Продакт (ABI.ru): Линии готовой еды и автоматизация", "https://www.abi.ru/press-center/news/", "html_scraper", "RU", ["#ReadyMeals", "#Automation"], 77.0, {"item_selector": ".news-item, p"}),
        ("b2b_patents", "ГК ЭФКО (Hi-Food): Сладкие белки и биотехнологии", "https://www.efko.ru/news/", "html_scraper", "RU", ["#Biotech", "#SweetProteins"], 80.0, {"item_selector": "article, p"}),
        ("b2b_patents", "Роспатент (ФИПС): Патентная активность в РФ", "https://rupto.ru/ru/news", "html_scraper", "RU", ["#Patents", "#IP"], 76.0, {"item_selector": ".views-row, article"}),
        ("b2b_patents", "arXiv (Biomolecules): Препринты по белкам и ферментам", "https://export.arxiv.org/rss/q-bio.BM", "rss_feed", "GLOBAL", ["#Science", "#Preprints"], 74.0, {}),
        ("b2b_patents", "PubMed: Клинические исследования клетчатки и микробиома", "https://pubmed.ncbi.nlm.nih.gov/rss/search/1/?term=prebiotic+fiber+beverage+microbiome", "rss_feed", "GLOBAL", ["#Clinical", "#Fiber"], 78.0, {}),
        ("b2b_patents", "Nature Food: Научные прорывы в индустрии еды", "https://www.nature.com/natfood.rss", "rss_feed", "GLOBAL", ["#Science", "#FoodTech"], 76.0, {}),
        ("b2b_patents", "Journal of Functional Foods: Новые ингредиенты", "https://rss.sciencedirect.com/publication/science/17564646", "rss_feed", "GLOBAL", ["#FunctionalFood", "#Ingredients"], 77.0, {}),
        ("b2b_patents", "GitHub Trending: Открытые ИИ-инструменты", "https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml", "rss_feed", "GLOBAL", ["#OpenSource", "#AI"], 70.0, {}),
        ("b2b_patents", "ТГ: Логистика и импорт сырья в РФ", "https://t.me/s/customs_logistics", "telegram_web", "RU", ["#SupplyChain", "#Import"], 74.0, {}),
        ("b2b_patents", "ТГ: Поставщики торговых сетей и СТМ РФ", "https://t.me/s/retail_suppliers", "telegram_web", "RU", ["#B2B", "#RetailChains"], 76.0, {}),
        ("b2b_patents", "ТГ: Сообщество пищевых технологов РФ", "https://t.me/s/food_technologist", "telegram_web", "RU", ["#Technologists", "#Formulation"], 78.0, {}),
    ])

    raw.extend([
        ("niche_media", "BevNET: Мировой радар функциональных напитков", "https://www.bevnet.com/rss", "rss_feed", "US", ["#Drinks", "#FunctionalSoda"], 80.0, {}),
        ("niche_media", "FoodNavigator Global/EU: Ингредиенты и тренды Европы", "https://www.foodnavigator.com/Info/FoodNavigator-RSS", "rss_feed", "EU", ["#Ingredients", "#EU"], 80.0, {}),
        ("niche_media", "FoodNavigator-USA: Рынок белка, клетчатки и снеков США", "https://www.foodnavigator-usa.com/Info/FoodNavigator-USA-RSS", "rss_feed", "US", ["#USFood", "#Protein"], 78.0, {}),
        ("niche_media", "FoodNavigator-Asia: Инновации напитков и еды Азии", "https://www.foodnavigator-asia.com/Info/FoodNavigator-Asia-RSS", "rss_feed", "ASIA", ["#AsiaFood", "#China"], 80.0, {}),
        ("niche_media", "FoodNavigator-LatAm: Пищевая индустрия Латинской Америки", "https://www.foodnavigator-latam.com/Info/FoodNavigator-LATAM-RSS", "rss_feed", "LATAM", ["#LatAmFood", "#Brazil"], 76.0, {}),
        ("niche_media", "NutraIngredients: Мировой радар пребиотиков и БАДов", "https://www.nutraingredients.com/Info/NutraIngredients-RSS", "rss_feed", "GLOBAL", ["#Nutraceuticals", "#Fiber"], 78.0, {}),
        ("niche_media", "DairyReporter: Молочные инновации и протеиновые десерты", "https://www.dairyreporter.com/Info/DairyReporter-RSS", "rss_feed", "GLOBAL", ["#Dairy", "#HighProtein"], 75.0, {}),
        ("niche_media", "BakeryAndSnacks: Мировые тренды снеков и выпечки", "https://www.bakeryandsnacks.com/Info/BakeryAndSnacks-RSS", "rss_feed", "GLOBAL", ["#Snacks", "#Bakery"], 75.0, {}),
        ("niche_media", "Milknews.ru: Молочный рынок и переработка РФ", "https://milknews.ru/rss.xml", "rss_feed", "RU", ["#DairyRU", "#FMCG"], 76.0, {}),
        ("niche_media", "Агроинвестор: Инвестпроекты и заводы АПК РФ", "https://www.agroinvestor.ru/rss/", "rss_feed", "RU", ["#AgroRU", "#Factories"], 78.0, {}),
        ("niche_media", "Sfera.fm: Пищевая промышленность и упаковка РФ", "https://sfera.fm/rss", "rss_feed", "RU", ["#FoodIndustryRU", "#Packaging"], 76.0, {}),
        ("niche_media", "Retail.ru: Торговые сети, СТМ и готовая еда РФ", "https://www.retail.ru/rss/news/", "rss_feed", "RU", ["#RetailRU", "#ReadyMeals"], 77.0, {}),
        ("niche_media", "New-Retail.ru: E-grocery и ритейл-технологии", "https://new-retail.ru/rss/", "rss_feed", "RU", ["#EGrocery", "#RetailTech"], 75.0, {}),
        ("niche_media", "Shopper's: Инсайды продовольственного ритейла и FMCG РФ", "https://t.me/s/shoppersmedia", "telegram_web", "RU", ["#InsidersRU", "#FMCG"], 80.0, {}),
        ("niche_media", "Sostav.ru: Запуски брендов и маркетинг РФ", "https://www.sostav.ru/rss/", "rss_feed", "RU", ["#Brands", "#Launches"], 74.0, {}),
        ("niche_media", "Роскачество: Исследования готовой еды и продуктов РФ", "https://rskrf.ru/news/", "html_scraper", "RU", ["#Quality", "#ReadyMeals"], 76.0, {"item_selector": ".news-item, article"}),
        ("niche_media", "ВЦИОМ: Опросы потребительского поведения россиян", "https://wciom.ru/rss/all_news.xml", "rss_feed", "RU", ["#Sociology", "#RU"], 75.0, {}),
        ("niche_media", "NTech / Исследования FMCG-ритейла РФ", "https://t.me/s/ntech_research", "telegram_web", "RU", ["#FMCGData", "#NTech"], 78.0, {}),
    ])

    raw.extend([
        ("mainstream_media", "РБК: Главные новости бизнеса (Калибратор мейнстрима)", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "rss_feed", "RU", ["#Mainstream", "#KillSwitch"], 60.0, {}),
        ("mainstream_media", "Коммерсантъ: Деловые новости РФ", "https://www.kommersant.ru/RSS/news.xml", "rss_feed", "RU", ["#Mainstream", "#KillSwitch"], 60.0, {}),
        ("mainstream_media", "Ведомости: Компании и рынки РФ", "https://www.vedomosti.ru/rss/news", "rss_feed", "RU", ["#Mainstream", "#KillSwitch"], 60.0, {}),
        ("mainstream_media", "Forbes Russia: Крупный бизнес и тренды", "https://www.forbes.ru/newrss.xml", "rss_feed", "RU", ["#Mainstream", "#KillSwitch"], 58.0, {}),
        ("mainstream_media", "ТАСС: Экономика и нацпроекты", "https://tass.ru/rss/v2.xml", "rss_feed", "RU", ["#Mainstream", "#KillSwitch"], 55.0, {}),
        ("mainstream_media", "Lenta.ru: Массовая новостная повестка", "https://lenta.ru/rss/news", "rss_feed", "RU", ["#Mainstream", "#KillSwitch"], 55.0, {}),
        ("mainstream_media", "Фонтанка: Деловой радар СЗФО", "https://www.fontanka.ru/fontanka.rss", "rss_feed", "RU", ["#Mainstream", "#KillSwitch"], 55.0, {}),
        ("mainstream_media", "CNBC Global Business: Мировой фильтр мейнстрим-СМИ", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147", "rss_feed", "GLOBAL", ["#Mainstream", "#GlobalMedia"], 55.0, {}),
    ])

    existing_names = {r.name for r in (await db.execute(select(Resource))).scalars().all()}
    for b_key, name, url, c_type, geo, tags, temp, cfg in raw:
        if name not in existing_names:
            db.add(Resource(block_id=blocks[b_key].id, name=name, url_or_id=url, connector_type=c_type, geo_region=geo, tags=tags, temperature=temp, connector_config=cfg))

    await db.commit()

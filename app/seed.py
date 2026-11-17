from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ConnectorCredential, AgentRoleConfig, SemanticBlock, Resource, AgentTelemetry

async def seed_initial_data(db: AsyncSession):
    if (await db.execute(select(SemanticBlock))).scalars().first():
        return

    db.add_all([
        ConnectorCredential(service_name="llm_openrouter", title="OpenRouter / OpenAI API Key (Hermes, Qwen-VL, Claude, DeepSeek)", api_key_or_token=""),
        ConnectorCredential(service_name="wb_mpstats", title="MPStats / Wildberries API Token (Запросы и новинки WB)", api_key_or_token=""),
        ConnectorCredential(service_name="ozon_api", title="Ozon Analytics API Key", api_key_or_token=""),
        ConnectorCredential(service_name="proxies", title="Пул прокси для Агента-Реаниматора", api_key_or_token=""),
    ])

    db.add_all([
        AgentRoleConfig(
            role_name="dirty_scout", title="1. Агент-Ищейка (Форумы, сленг, отзывы, вакансии)",
            provider="openrouter", model_name="nousresearch/hermes-3-llama-3.1-70b",
            system_prompt='Ты полевой разведчик трендов. Ищи жалобы покупателей, кустарные рецепты, новые ингредиенты и вакансии/заводы. Отвечай СТРОГО в JSON: {"is_anomaly": true/false, "confirmed": true/false, "essence": "суть", "summary": "детали", "multilang_keywords": ["ключ_ru", "key_en"]}'
        ),
        AgentRoleConfig(
            role_name="vision_meme", title="2. Визуальный Агент (Мемы, картинки, упаковка)",
            provider="openrouter", model_name="qwen/qwen-2.5-vl-72b-instruct",
            system_prompt='Ты визуальный разведчик. Ищи на картинках новые мемы и необычные продукты. Отвечай СТРОГО в JSON: {"is_anomaly": true/false, "essence": "суть визуала", "multilang_keywords": ["тег1", "tag2"]}'
        ),
        AgentRoleConfig(
            role_name="synthesizer", title="3. Агент-Контекстуализатор (Ведение Файла Памяти)",
            provider="openrouter", model_name="anthropic/claude-3.5-sonnet",
            system_prompt='Синтезируй сигнал и подтверждения для Файла Памяти. Отвечай СТРОГО в JSON: {"title": "Имя тренда", "core_essence": "Суть сдвига", "delta_summary": "Что нового"}'
        ),
        AgentRoleConfig(
            role_name="foreman_brain", title="4. Мозг Агента-Прораба (Координация и баланс роя)",
            provider="openrouter", model_name="nousresearch/hermes-3-llama-3.1-70b",
            system_prompt='Ты Агент-Прораб. Балансируй внимание роя между смысловыми блоками. Отвечай в JSON: {"boost_block": "investments"}'
        ),
        AgentRoleConfig(
            role_name="memory_analyst", title="5. Агент Пост-Анализа Памяти (Сшивка трендов в Инвест-Гипотезы)",
            provider="openrouter", model_name="deepseek/deepseek-r1",
            system_prompt='Ты Стратег Пост-Анализа Файла Памяти. Связывай разные тренды (боли + лайфхаки + инвестиции/заводы) в бизнес-гипотезы. Отвечай СТРОГО в JSON: {"title": "Мета-инсайт", "linked_codes": ["TREND-0001"], "pattern_type": "PAIN_TO_INVESTMENT", "consumer_pain_summary": "Боль", "investment_or_product_thesis": "Инвест/продуктовый вывод", "confidence_score": 0.85}'
        ),
    ])

    blocks = {
        "consumer": SemanticBlock(name="1. Потребительский опыт, Маркетплейсы и Отзовики", block_type="consumer", description="WB, Ozon, ВкусВилл, Самокат, Отзовики, Amazon, iHerb"),
        "underground": SemanticBlock(name="2. Форумы, Имиджборды, Мемы и Кустарные рецепты", block_type="underground", description="Двач, Пикабу, Reddit (20 сабреддитов), Азия, Латинская Америка, MENA"),
        "investments": SemanticBlock(name="3. Инвестиции, Биржа, Венчур и Стартапы", block_type="investments", description="Смартлаб, Пульс, SeekingAlpha, AgFunder, FoodHack, мировые VC"),
        "b2b_patents": SemanticBlock(name="4. B2B, Вакансии, Заводы, Патенты и Наука", block_type="b2b_patents", description="API HH.ru, Минсельхозы регионов, Роспатент, PubMed, arXiv, ЭФКО, АБИ"),
        "niche_media": SemanticBlock(name="5. Отраслевые СМИ и Исследовательские Центры", block_type="niche_media", description="BevNET, FoodNavigator (5 регионов), Milknews, Retail.ru, Роскачество, ВЦИОМ"),
        "mainstream_media": SemanticBlock(name="6. Мейнстрим-СМИ (Калибратор / Фильтр «Уже в газетах»)", block_type="mainstream_media", description="РБК, Коммерсантъ, Ведомости, Forbes, ТАСС, CNBC"),
    }
    for b in blocks.values():
        db.add(b)
    await db.flush()

    raw = [
        ("consumer", "WB: Поисковые тренды Напитки и Клетчатка", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#FMCG", "#Drinks"], 90.0, {"default_query": "напиток клетчатка пребиотик без сахара"}),
        ("consumer", "WB: Новинки Готовая еда и Белковые снеки", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#FMCG", "#Snacks"], 86.0, {"default_query": "высокобелковый снек протеиновый перекус"}),
        ("consumer", "WB: Новинки БАДы, Грибы и Адаптогены", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#Supplements", "#Biohacking"], 85.0, {"default_query": "псиллиум инулин ежовик кордицепс"}),
        ("consumer", "WB: Новинки Косметика и Пептиды", "https://mpstats.io/api/wb/get/search", "wb_mpstats", "RU", ["#Cosmetics", "#Skincare"], 80.0, {"default_query": "пептиды спикулы центелла пробиотик"}),
        ("consumer", "Ozon / Ozon Fresh: Продуктовые запуски", "https://t.me/s/ozonhq", "telegram_web", "RU", ["#Ecom", "#FMCG"], 78.0, {}),
        ("consumer", "Яндекс Лавка: Новинки готовой еды и СТМ", "https://t.me/s/yandex_lavka", "telegram_web", "RU", ["#ReadyMeals", "#Delivery"], 82.0, {}),
        ("consumer", "ВкусВилл: Отзывы и народные обзоры новинок", "https://t.me/s/vkusvill_official", "telegram_web", "RU", ["#ReadyMeals", "#CleanLabel"], 86.0, {}),
        ("consumer", "Самокат: Новинки дарксторов и снеков", "https://t.me/s/samokat_15min", "telegram_web", "RU", ["#Delivery", "#FMCG"], 80.0, {}),
        ("consumer", "Чижик (X5): Обзоры дискаунтеров и СТМ", "https://t.me/s/chizhik_official", "telegram_web", "RU", ["#Discounter", "#Retail"], 75.0, {}),
        ("consumer", "ТГ: Новинки с полок супермаркетов РФ", "https://t.me/s/produkty_v_setyah", "telegram_web", "RU", ["#Retail", "#NewArrivals"], 76.0, {}),
        ("consumer", "IRecommend: Отзывы Продукты питания", "https://irecommend.ru/taxonomy/term/938/rss.xml", "rss_feed", "RU", ["#Reviews", "#FMCG"], 82.0, {}),
        ("consumer", "IRecommend: Отзывы Диеты и БАДы", "https://irecommend.ru/taxonomy/term/87/rss.xml", "rss_feed", "RU", ["#Reviews", "#Supplements"], 80.0, {}),
        ("consumer", "IRecommend: Отзывы Косметика", "https://irecommend.ru/taxonomy/term/30/rss.xml", "rss_feed", "RU", ["#Reviews", "#Cosmetics"], 75.0, {}),
        ("consumer", "Otzovik: Отзывы о продуктах питания", "https://otzovik.com/reviews/produkti_pitaniya/", "html_scraper", "RU", ["#Reviews", "#ConsumerPain"], 80.0, {"item_selector": ".item"}),
        ("consumer", "Otzovik: Отзывы о доставке готовой еды", "https://otzovik.com/reviews/dostavka_edi/", "html_scraper", "RU", ["#ReadyMeals", "#ConsumerPain"], 84.0, {"item_selector": ".item"}),
        ("consumer", "Babyblog: Женский форум о питании и покупках", "https://www.babyblog.ru/community/lenta/food", "html_scraper", "RU", ["#Family", "#FMCG"], 70.0, {"item_selector": "article"}),
        ("consumer", "Woman.ru: Форум диет и новых продуктов", "https://www.woman.ru/health/diets/forum/", "html_scraper", "RU", ["#Diet", "#Women"], 70.0, {"item_selector": ".card"}),
        ("consumer", "Littleone: Форум покупок и супермаркетов", "https://forum.littleone.ru/forumdisplay.php?f=85", "html_scraper", "RU", ["#Retail", "#Family"], 65.0, {"item_selector": ".threadtitle"}),
        ("consumer", "Amazon Movers & Shakers: Лидеры роста Grocery (США)", "https://www.amazon.com/gp/movers-and-shakers/grocery", "html_scraper", "US", ["#GlobalTrends", "#Grocery"], 85.0, {"item_selector": ".zg-grid-general-faceout"}),
        ("consumer", "Amazon Movers & Shakers: Здоровье и БАДы (США)", "https://www.amazon.com/gp/movers-and-shakers/hpc", "html_scraper", "US", ["#Supplements", "#US"], 82.0, {"item_selector": ".zg-grid-general-faceout"}),
        ("consumer", "iHerb Trending: Мировые хиты клетчатки и нутрицевтики", "https://www.iherb.com/catalog/topsellers", "html_scraper", "GLOBAL", ["#Fiber", "#Supplements"], 80.0, {"item_selector": ".product-cell-container"}),
        ("consumer", "Product Hunt: Новые Food & Health приложения", "https://www.producthunt.com/feed?category=health-fitness", "rss_feed", "GLOBAL", ["#Apps", "#HealthTech"], 75.0, {}),
    ]

    for board, desc, tag, temp in [
        ("cook", "Кулинария и крафтовые смеси", "#DIY", 85.0), ("fit", "Спортпит, белок и предтрены", "#Protein", 80.0),
        ("fag", "Ранние вирусные тренды блогов", "#Viral", 72.0), ("b", "Генератор рунет-мемов и картинок", "#Memes", 70.0),
        ("biz", "Малый бизнес, цеха и селлеры", "#Sellers", 78.0), ("sci", "Химия, ноотропы и ингредиенты", "#Science", 74.0)
    ]:
        raw.append(("underground", f"Двач (2ch.hk): /{board}/ ({desc})", f"https://2ch.hk/{board}/catalog.json", "json_api", "RU", [tag, "#Imageboard"], temp, {}))

    raw.extend([
        ("underground", "Пикабу: Свежее и вирусные посты РФ", "https://pikabu.ru/xmlfeeds.php?cmd=popular", "rss_feed", "RU", ["#Viral", "#RU"], 78.0, {}),
        ("underground", "ЯПлакалъ: Народные видео и мемы РФ", "https://www.yaplakal.com/news.xml", "rss_feed", "RU", ["#Memes", "#Viral"], 70.0, {}),
        ("underground", "KnowYourMeme: Мировой радар зарождающихся мемов", "https://knowyourmeme.com/newsfeed.rss", "rss_feed", "GLOBAL", ["#Memes", "#Global"], 78.0, {}),
        ("underground", "ТГ: Тренды Китая Douyin и Xiaohongshu", "https://t.me/s/china_business_trends", "telegram_web", "CN", ["#China", "#Douyin"], 85.0, {}),
    ])

    for sub, geo, tag, temp in [
        ("Supplements", "GLOBAL", "#Supplements", 88.0), ("foodscience", "GLOBAL", "#FoodTech", 90.0),
        ("GutHealth", "GLOBAL", "#Fiber", 89.0), ("Biohackers", "GLOBAL", "#Biohacking", 85.0),
        ("fermentation", "GLOBAL", "#Fermentation", 84.0), ("Ozempic", "US", "#GLP1", 86.0),
        ("GroceryStores", "US", "#Retail", 78.0), ("snacks", "GLOBAL", "#Snacks", 82.0),
        ("energydrinks", "GLOBAL", "#Drinks", 80.0), ("tea", "ASIA", "#AsianTrends", 75.0),
        ("coffee", "GLOBAL", "#Coffee", 76.0), ("Nootropics", "GLOBAL", "#Nootropics", 82.0),
        ("AsianBeauty", "ASIA", "#Cosmetics", 80.0), ("KoreanFood", "ASIA", "#Korea", 79.0),
        ("JapaneseFood", "ASIA", "#Japan", 78.0), ("brasil", "LATAM", "#LatAm", 73.0),
        ("mexico", "LATAM", "#LatAm", 73.0), ("india", "IN", "#India", 74.0),
        ("dubai", "MENA", "#MENA", 77.0), ("Thailand", "ASIA", "#SEA", 75.0)
    ]:
        raw.append(("underground", f"Reddit: r/{sub} ({geo})", f"https://www.reddit.com/r/{sub}/new.json", "json_api", geo, [tag, "#Reddit"], temp, {}))

    raw.append(("investments", "Smart-Lab: Все блоги инвесторов РФ", "https://smart-lab.ru/rss/all/", "rss_feed", "RU", ["#StocksRU", "#SmartMoney"], 88.0, {}))
    for ticker, name in [("MGNT", "Магнит"), ("FIVE", "X5 Retail"), ("GCHE", "Черкизово"), ("ABRD", "Абрау-Дюрсо"), ("AQUA", "Инарктика"), ("AGRO", "Русагро"), ("BELU", "НоваБев")]:
        raw.append(("investments", f"Smart-Lab: Форум акций {name} ({ticker})", f"https://smart-lab.ru/forum/{ticker}", "html_scraper", "RU", [f"#{ticker}", "#StocksRU"], 78.0, {"item_selector": ".comment_text"}))

    raw.extend([
        ("investments", "Т-Инвестиции (Пульс): Сентимент розничных инвесторов", "https://t.me/s/tinkoff_invest_official", "telegram_web", "RU", ["#Sentiment", "#StocksRU"], 78.0, {}),
        ("investments", "Reddit: r/wallstreetbets (Биржевой хайп)", "https://www.reddit.com/r/wallstreetbets/new.json", "json_api", "US", ["#MemeStocks", "#US"], 75.0, {}),
        ("investments", "Reddit: r/investing (Инвест-идеи сектора потребления)", "https://www.reddit.com/r/investing/new.json", "json_api", "GLOBAL", ["#Investing", "#Macro"], 78.0, {}),
        ("investments", "Reddit: r/SecurityAnalysis (Отчеты инвестфондов)", "https://www.reddit.com/r/SecurityAnalysis/new.json", "json_api", "GLOBAL", ["#DeepValue", "#Equity"], 80.0, {}),
        ("investments", "Seeking Alpha: Аналитика Consumer Staples & FMCG", "https://seekingalpha.com/market_currents.xml", "rss_feed", "US", ["#FMCG", "#Earnings"], 82.0, {}),
        ("investments", "Hacker News (YC): Стартапы и технологии", "https://hnrss.org/newest?points=50", "rss_feed", "GLOBAL", ["#Startups", "#YC"], 84.0, {}),
        ("investments", "TechCrunch Venture: Мировые венчурные сделки", "https://techcrunch.com/category/venture/feed/", "rss_feed", "GLOBAL", ["#Venture", "#Seed"], 80.0, {}),
        ("investments", "AgFunderNews: Венчурные раунды FoodTech и AgTech", "https://agfundernews.com/feed", "rss_feed", "GLOBAL", ["#FoodTech", "#AgTech"], 92.0, {}),
        ("investments", "Green Queen (Азия): Инвестиции в ферментацию и белки", "https://www.greenqueen.com.hk/feed/", "rss_feed", "ASIA", ["#Fermentation", "#AltProtein"], 88.0, {}),
        ("investments", "FoodHack (Швейцария): Европейские сделки FoodTech", "https://foodhack.global/rss", "html_scraper", "EU", ["#FoodTech", "#EuropeVC"], 85.0, {"item_selector": "article, p"}),
        ("investments", "EU-Startups: Раунды потребительских брендов Европы", "https://www.eu-startups.com/feed/", "rss_feed", "EU", ["#EuropeVC", "#D2C"], 78.0, {}),
        ("investments", "LatAm List: Венчур Латинской Америки", "https://latamlist.com/feed/", "rss_feed", "LATAM", ["#LatAmVC", "#Startups"], 76.0, {}),
        ("investments", "Wamda (MENA/Дубай): Инвестиции Ближнего Востока", "https://www.wamda.com/feed", "rss_feed", "MENA", ["#MENAVC", "#Dubai"], 76.0, {}),
        ("investments", "Inc42 (Индия): Сделки D2C и FMCG Индии", "https://inc42.com/feed/", "rss_feed", "IN", ["#IndiaVC", "#FMCG"], 77.0, {}),
    ])

    for q_title, q_text in [
        ("Технолог пищевого производства и R&D", "технолог пищевого производства OR разработка рецептур"),
        ("Белок, Ферментация и Клетчатка", "ферментация OR изолят белка OR пищевые волокна OR пребиотик"),
        ("Готовая еда и Фабрики-кухни", "фабрика кухня OR производство готовой еды OR технолог готовых блюд"),
        ("Функциональные напитки и Розлив", "технолог безалкогольных напитков OR розлив напитков OR купажист"),
        ("Разработка СТМ в ритейле", "менеджер СТМ продукты питания OR разработка СТМ FMCG"),
    ]:
        raw.append(("b2b_patents", f"HeadHunter API: Вакансии — {q_title}", "https://api.hh.ru/vacancies", "hh_vacancies", "RU", ["#Jobs", "#B2B"], 90.0, {"search_text": q_text}))

    raw.extend([
        ("b2b_patents", "Минсельхозпрод Московской области: Заводы и проекты", "https://msh.mosreg.ru/novosti", "html_scraper", "RU", ["#Gov", "#Factories"], 86.0, {"item_selector": ".news-item, article, h3"}),
        ("b2b_patents", "Минсельхоз Краснодарского края: Проекты пищепрома Юга", "https://msh.krasnodar.ru/news/", "html_scraper", "RU", ["#Gov", "#Factories"], 80.0, {"item_selector": ".news-item, article"}),
        ("b2b_patents", "Инвестпортал Татарстана: Новые производства", "https://tida.tatarstan.ru/rus/index.htm/news", "html_scraper", "RU", ["#Gov", "#Factories"], 78.0, {"item_selector": ".news-list, p"}),
        ("b2b_patents", "Инвестпортал Новосибирской области: Проекты Сибири", "https://air-nso.ru/news/", "html_scraper", "RU", ["#Gov", "#Factories"], 78.0, {"item_selector": "article, h2"}),
        ("b2b_patents", "АБИ Продакт (ABI.ru): Линии готовой еды и автоматизация", "https://www.abi.ru/press-center/news/", "html_scraper", "RU", ["#ReadyMeals", "#Automation"], 82.0, {"item_selector": ".news-item, p"}),
        ("b2b_patents", "ГК ЭФКО (Hi-Food): Сладкие белки и биотехнологии", "https://www.efko.ru/news/", "html_scraper", "RU", ["#Biotech", "#SweetProteins"], 88.0, {"item_selector": "article, p"}),
        ("b2b_patents", "Роспатент (ФИПС): Патентная активность в РФ", "https://rupto.ru/ru/news", "html_scraper", "RU", ["#Patents", "#IP"], 80.0, {"item_selector": ".views-row, article"}),
        ("b2b_patents", "arXiv (Biomolecules): Препринты по белкам и ферментам", "https://export.arxiv.org/rss/q-bio.BM", "rss_feed", "GLOBAL", ["#Science", "#Preprints"], 78.0, {}),
        ("b2b_patents", "PubMed: Клинические исследования клетчатки и микробиома", "https://pubmed.ncbi.nlm.nih.gov/rss/search/1/?term=prebiotic+fiber+beverage+microbiome", "rss_feed", "GLOBAL", ["#Clinical", "#Fiber"], 84.0, {}),
        ("b2b_patents", "Nature Food: Научные прорывы в индустрии еды", "https://www.nature.com/natfood.rss", "rss_feed", "GLOBAL", ["#Science", "#FoodTech"], 82.0, {}),
        ("b2b_patents", "Journal of Functional Foods: Новые ингредиенты", "https://rss.sciencedirect.com/publication/science/17564646", "rss_feed", "GLOBAL", ["#FunctionalFood", "#Ingredients"], 84.0, {}),
        ("b2b_patents", "GitHub Trending: Открытые ИИ-инструменты", "https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml", "rss_feed", "GLOBAL", ["#OpenSource", "#AI"], 70.0, {}),
        ("b2b_patents", "ТГ: Логистика и импорт сырья в РФ", "https://t.me/s/customs_logistics", "telegram_web", "RU", ["#SupplyChain", "#Import"], 76.0, {}),
        ("b2b_patents", "ТГ: Поставщики торговых сетей и СТМ РФ", "https://t.me/s/retail_suppliers", "telegram_web", "RU", ["#B2B", "#RetailChains"], 80.0, {}),
        ("b2b_patents", "ТГ: Сообщество пищевых технологов РФ", "https://t.me/s/food_technologist", "telegram_web", "RU", ["#Technologists", "#Formulation"], 86.0, {}),
    ])

    raw.extend([
        ("niche_media", "BevNET: Мировой радар функциональных напитков", "https://www.bevnet.com/rss", "rss_feed", "US", ["#Drinks", "#FunctionalSoda"], 90.0, {}),
        ("niche_media", "FoodNavigator Global/EU: Ингредиенты и тренды Европы", "https://www.foodnavigator.com/Info/FoodNavigator-RSS", "rss_feed", "EU", ["#Ingredients", "#EU"], 90.0, {}),
        ("niche_media", "FoodNavigator-USA: Рынок белка, клетчатки и снеков США", "https://www.foodnavigator-usa.com/Info/FoodNavigator-USA-RSS", "rss_feed", "US", ["#USFood", "#Protein"], 88.0, {}),
        ("niche_media", "FoodNavigator-Asia: Инновации напитков и еды Азии", "https://www.foodnavigator-asia.com/Info/FoodNavigator-Asia-RSS", "rss_feed", "ASIA", ["#AsiaFood", "#China"], 90.0, {}),
        ("niche_media", "FoodNavigator-LatAm: Пищевая индустрия Латинской Америки", "https://www.foodnavigator-latam.com/Info/FoodNavigator-LATAM-RSS", "rss_feed", "LATAM", ["#LatAmFood", "#Brazil"], 84.0, {}),
        ("niche_media", "NutraIngredients: Мировой радар пребиотиков и БАДов", "https://www.nutraingredients.com/Info/NutraIngredients-RSS", "rss_feed", "GLOBAL", ["#Nutraceuticals", "#Fiber"], 88.0, {}),
        ("niche_media", "DairyReporter: Молочные инновации и протеиновые десерты", "https://www.dairyreporter.com/Info/DairyReporter-RSS", "rss_feed", "GLOBAL", ["#Dairy", "#HighProtein"], 80.0, {}),
        ("niche_media", "BakeryAndSnacks: Мировые тренды снеков и выпечки", "https://www.bakeryandsnacks.com/Info/BakeryAndSnacks-RSS", "rss_feed", "GLOBAL", ["#Snacks", "#Bakery"], 82.0, {}),
        ("niche_media", "Milknews.ru: Молочный рынок и переработка РФ", "https://milknews.ru/rss.xml", "rss_feed", "RU", ["#DairyRU", "#FMCG"], 82.0, {}),
        ("niche_media", "Агроинвестор: Инвестпроекты и заводы АПК РФ", "https://www.agroinvestor.ru/rss/", "rss_feed", "RU", ["#AgroRU", "#Factories"], 84.0, {}),
        ("niche_media", "Sfera.fm: Пищевая промышленность и упаковка РФ", "https://sfera.fm/rss", "rss_feed", "RU", ["#FoodIndustryRU", "#Packaging"], 82.0, {}),
        ("niche_media", "Retail.ru: Торговые сети, СТМ и готовая еда РФ", "https://www.retail.ru/rss/news/", "rss_feed", "RU", ["#RetailRU", "#ReadyMeals"], 85.0, {}),
        ("niche_media", "New-Retail.ru: E-grocery и ритейл-технологии", "https://new-retail.ru/rss/", "rss_feed", "RU", ["#EGrocery", "#RetailTech"], 80.0, {}),
        ("niche_media", "Shopper's: Инсайды продовольственного ритейла и FMCG РФ", "https://t.me/s/shoppersmedia", "telegram_web", "RU", ["#InsidersRU", "#FMCG"], 88.0, {}),
        ("niche_media", "Sostav.ru: Запуски брендов и маркетинг РФ", "https://www.sostav.ru/rss/", "rss_feed", "RU", ["#Brands", "#Launches"], 76.0, {}),
        ("niche_media", "Роскачество: Исследования готовой еды и продуктов РФ", "https://rskrf.ru/news/", "html_scraper", "RU", ["#Quality", "#ReadyMeals"], 82.0, {"item_selector": ".news-item, article"}),
        ("niche_media", "ВЦИОМ: Опросы потребительского поведения россиян", "https://wciom.ru/rss/all_news.xml", "rss_feed", "RU", ["#Sociology", "#RU"], 78.0, {}),
        ("niche_media", "NTech / Исследования FMCG-ритейла РФ", "https://t.me/s/ntech_research", "telegram_web", "RU", ["#FMCGData", "#NTech"], 85.0, {}),
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

    for b_key, name, url, c_type, geo, tags, temp, cfg in raw:
        db.add(Resource(block_id=blocks[b_key].id, name=name, url_or_id=url, connector_type=c_type, geo_region=geo, tags=tags, temperature=temp, connector_config=cfg))

    for i in range(1, 5):
        db.add(AgentTelemetry(agent_id=f"Scout-{i}", agent_type="SCOUT", state="IDLE", current_resource="-", current_action="Готов к полевому выходу"))
    db.add(AgentTelemetry(agent_id="Foreman-Прораб", agent_type="FOREMAN", state="IDLE", current_resource="Весь Рой", current_action="Координация 6 смысловых блоков"))
    db.add(AgentTelemetry(agent_id="Reanimator-Реаниматор", agent_type="REANIMATOR", state="IDLE", current_resource="Инфраструктура", current_action="Мониторинг локов, ошибок и штата ищеек"))
    db.add(AgentTelemetry(agent_id="Analyst-Память", agent_type="MEMORY_ANALYST", state="IDLE", current_resource="Файл Памяти", current_action="Пост-анализ трендов и сшивка инвест-гипотез"))

    await db.commit()

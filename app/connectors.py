import time
import json
import httpx
import feedparser
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Resource, ConnectorCredential

class ResourceConnectorEngine:
    # Глобальный пул соединений Keep-Alive (убирает паразитный TX-трафик на TLS Handshake)
    _client: httpx.AsyncClient | None = None
    # Кеш ETag / Last-Modified для получения 304 Not Modified (0 байт трафика)
    _http_Validators: dict[str, dict] = {}
    # Локальный кеш распарсенных айтемов (TTL 15 минут), чтобы CROSS_CHECKING не качал сайты заново!
    _items_cache: dict[int, tuple[float, list[dict]]] = {}

    MAX_RESPONSE_BYTES = 220_000  # Жесткий лимит 220 КБ на страницу (отсекает 5-мегабайтные хвосты)
    CACHE_TTL_SECONDS = 900       # 15 минут жизни локального кеша для перекрестных проверок

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            cls._client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=25, max_connections=50, keepalive_expiry=60.0),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                    "Accept-Encoding": "gzip, deflate"
                }
            )
        return cls._client

    @classmethod
    async def fetch_raw_capped(cls, url: str, params: dict = None, extra_headers: dict = None, use_304: bool = True) -> tuple[int, bytes]:
        \"\"\"Качает максимум 220 КБ в потоке + поддерживает 304 Not Modified (0 байт при отсутствии изменений)\"\"\"
        client = cls.get_client()
        req_headers = dict(extra_headers or {})
        cache_key = f"{url}?{json.dumps(params, sort_keys=True)}" if params else url

        if use_304 and cache_key in cls._http_Validators:
            v = cls._http_Validators[cache_key]
            if v.get("etag"):
                req_headers["If-None-Match"] = v["etag"]
            if v.get("last_modified"):
                req_headers["If-Modified-Since"] = v["last_modified"]

        async with client.stream("GET", url, params=params, headers=req_headers) as resp:
            if resp.status_code == 304:
                return 304, b""
            if resp.status_code >= 400:
                return resp.status_code, b""

            if use_304:
                etag = resp.headers.get("ETag")
                lm = resp.headers.get("Last-Modified")
                if etag or lm:
                    cls._http_Validators[cache_key] = {"etag": etag, "last_modified": lm}

            chunks = []
            downloaded = 0
            async for chunk in resp.aiter_bytes(chunk_size=16384):
                chunks.append(chunk)
                downloaded += len(chunk)
                if downloaded >= cls.MAX_RESPONSE_BYTES:
                    break
            return resp.status_code, b"".join(chunks)

    @staticmethod
    async def get_credentials(db: AsyncSession, service_name: str) -> ConnectorCredential | None:
        res = await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == service_name))
        return res.scalars().first()

    @classmethod
    async def fetch(cls, db: AsyncSession, resource: Resource, search_query: str = None) -> list[dict]:
        now = time.time()
        # 1. Если это перекрестная проверка (search_query) и ресурс уже есть в локальном кеше — берем из памяти за 0 байт!
        if resource.id in cls._items_cache:
            cached_time, cached_items = cls._items_cache[resource.id]
            if (now - cached_time) < cls.CACHE_TTL_SECONDS:
                if search_query:
                    q_low = search_query.lower()
                    matched = [i for i in cached_items if q_low in i["text"].lower()]
                    return matched if matched else cached_items[:4]
                return cached_items

        ctype = resource.connector_type
        cfg = resource.connector_config or {}
        items = []

        # 1. КОННЕКТОР ВАКАНСИЙ HEADHUNTER (Запрашиваем только 8 свежих записей вместо 15)
        if ctype == "hh_vacancies":
            text_filter = search_query or cfg.get("search_text", "технолог пищевого производства OR FMCG")
            status, raw_bytes = await cls.fetch_raw_capped(
                resource.url_or_id,
                params={"text": text_filter, "per_page": 8, "order_by": "publication_time"},
                use_304=(search_query is None)
            )
            if status == 200 and raw_bytes:
                data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                items = [{
                    "author_id": v.get("employer", {}).get("name", "company"),
                    "text": f"Компания: {v.get('employer', {}).get('name')}. Вакансия: {v.get('name')}. Требования: {v.get('snippet', {}).get('requirement')}",
                    "image_url": None
                } for v in data.get("items", [])[:8]]

        # 2. КОННЕКТОР МАРКЕТПЛЕЙСОВ (Wildberries / MPStats)
        elif ctype == "wb_mpstats":
            cred = await cls.get_credentials(db, "wb_mpstats")
            if cred and cred.api_key_or_token:
                status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, extra_headers={"X-Mpstats-TOKEN": cred.api_key_or_token})
                if status == 200 and raw_bytes:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                    items = [{"author_id": "wb_mpstats", "text": str(item), "image_url": None} for item in data[:10]]
            else:
                q = search_query or cfg.get("default_query", "напиток клетчатка протеин снек")
                wb_url = f"https://search.wb.ru/exactmatch/ru/common/v7/search?ab_testing=false&appType=1&curr=rub&dest=-1257786&query={q}&resultset=catalog&sort=Newly"
                status, raw_bytes = await cls.fetch_raw_capped(wb_url, use_304=(search_query is None))
                if status == 200 and raw_bytes:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                    items = [{
                        "author_id": p.get("brand", "seller"),
                        "text": f"Новинка WB: {p.get('name')} | Бренд: {p.get('brand')} | Отзывов: {p.get('feedbacks')}",
                        "image_url": None
                    } for p in data.get("data", {}).get("products", [])[:10]]

        # 3. КОННЕКТОР TELEGRAM
        elif ctype == "telegram_web":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True)
            if status == 200 and raw_bytes:
                soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="ignore"), "html.parser")
                for msg in soup.select(".tgme_widget_message")[-10:]:
                    t_el = msg.select_one(".tgme_widget_message_text")
                    p_el = msg.select_one(".tgme_widget_message_photo_wrap")
                    img = p_el["style"].split("url('")[1].split("')")[0] if (p_el and "url('" in p_el.get("style", "")) else None
                    if t_el or img:
                        items.append({"author_id": msg.get("data-post", "tg"), "text": t_el.get_text(" ", strip=True) if t_el else "[Мем/Фото]", "image_url": img})

        # 4. КОННЕКТОР JSON (Reddit с жестким ?limit=8 и Двач)
        elif ctype == "json_api":
            url = resource.url_or_id
            # Урезаем вес ответа Reddit в 10 раз (с 500 КБ до 40 КБ)
            if "reddit.com" in url and "limit=" not in url:
                url = f"{url}?limit=8&raw_json=1"
            # Для Двача вместо 5-мегабайтного catalog.json берем легкий список тредов threads.json (~40 КБ!)
            if "2ch.hk" in url and "catalog.json" in url:
                url = url.replace("catalog.json", "threads.json")

            status, raw_bytes = await cls.fetch_raw_capped(url, use_304=True)
            if status == 200 and raw_bytes:
                data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                if isinstance(data, dict) and "data" in data and "children" in data["data"]:
                    for post in data["data"]["children"][:8]:
                        pd = post.get("data", {})
                        img = pd.get("url") if str(pd.get("url", "")).endswith((".jpg", ".png", ".webp")) else None
                        items.append({"author_id": pd.get("author", "redditor"), "text": f"{pd.get('title', '')} - {pd.get('selftext', '')[:400]}", "image_url": img})
                elif isinstance(data, dict) and "threads" in data:
                    for th in data["threads"][:10]:
                        op = th["posts"][0] if "posts" in th else th
                        txt = BeautifulSoup(op.get("comment", ""), "html.parser").get_text(" ", strip=True)
                        items.append({"author_id": str(op.get("num", "anon")), "text": f"{op.get('subject', '')}: {txt[:400]}", "image_url": None})

        # 5. КОННЕКТОР RSS / ATOM
        elif ctype == "rss_feed":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True)
            if status == 200 and raw_bytes:
                feed = feedparser.parse(raw_bytes.decode("utf-8", errors="ignore"))
                for entry in feed.entries[:10]:
                    summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
                    full = f"{entry.get('title', '')}. {summary}"
                    items.append({"author_id": entry.get("author", resource.name), "text": full[:550], "image_url": None})

        # 6. HTML-СКРЕЙПЕР (С потолком 220 КБ)
        elif ctype == "html_scraper":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True)
            if status == 200 and raw_bytes:
                soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="ignore"), "html.parser")
                sel = cfg.get("item_selector", "article, .item, .news-item, .comment_text, p")
                items = [{"author_id": resource.name, "text": el.get_text(" ", strip=True)[:500], "image_url": None} for el in soup.select(sel)[:10] if len(el.get_text(strip=True)) > 40]

        if items:
            cls._items_cache[resource.id] = (now, items)

        if search_query and items:
            q_low = search_query.lower()
            matched = [i for i in items if q_low in i["text"].lower()]
            return matched
        return items

import time
import json
import httpx
import feedparser
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Resource, ConnectorCredential

class ResourceConnectorEngine:
    _client: httpx.AsyncClient | None = None
    _current_proxy: str = ""
    _http_validators: dict[str, dict] = {}
    _items_cache: dict[int, tuple[float, list[dict]]] = {}

    MAX_RESPONSE_BYTES = 180_000
    CACHE_TTL_SECONDS = 1500
    MAX_CACHE_KEYS = 250

    @classmethod
    def get_client(cls, proxy_url: str = "") -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed or cls._current_proxy != proxy_url:
            cls._current_proxy = proxy_url
            kwargs = {
                "timeout": 15.0,
                "follow_redirects": True,
                "limits": httpx.Limits(max_keepalive_connections=20, max_connections=40, keepalive_expiry=60.0),
                "headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                    "Accept-Encoding": "gzip, deflate"
                }
            }
            if proxy_url:
                kwargs["proxy"] = proxy_url.split(",")[0].strip()
            cls._client = httpx.AsyncClient(**kwargs)
        return cls._client

    @classmethod
    def search_block_cache(cls, resources: list[Resource], keywords: list[str]) -> list[tuple[Resource, list[dict]]]:
        """
        Мгновенный поиск совпадений сразу по ВСЕМ закешированным сайтам целевого блока (0 байт сетевого трафика!).
        Проверяет вхождение корней ключевых слов (минимум 5 символов).
        """
        stems = [k.lower()[:6] for k in (keywords or []) if len(k.strip()) >= 4]
        if not stems:
            return []

        hits = []
        for res in resources:
            if res.id not in cls._items_cache:
                continue
            _, cached_items = cls._items_cache[res.id]
            matched_items = [
                item for item in cached_items
                if any(st in item.get("text", "").lower() for st in stems)
            ]
            if matched_items:
                hits.append((res, matched_items))
        return hits

    @classmethod
    async def fetch_raw_capped(cls, url: str, params: dict = None, extra_headers: dict = None, use_304: bool = True, proxy_url: str = "") -> tuple[int, bytes]:
        if len(cls._http_validators) > cls.MAX_CACHE_KEYS:
            cls._http_validators.clear()

        client = cls.get_client(proxy_url)
        req_headers = dict(extra_headers or {})
        if "reddit.com" in url:
            req_headers["User-Agent"] = "linux:trend.swarm.analytics:v2.5 (by /u/trend_research_bot)"

        cache_key = f"{url}?{json.dumps(params, sort_keys=True)}" if params else url
        if use_304 and cache_key in cls._http_validators:
            v = cls._http_validators[cache_key]
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
                    cls._http_validators[cache_key] = {"etag": etag, "last_modified": lm}

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
        if len(cls._items_cache) > cls.MAX_CACHE_KEYS:
            cls._items_cache = {k: v for k, v in cls._items_cache.items() if (now - v[0]) < cls.CACHE_TTL_SECONDS}

        if resource.id in cls._items_cache:
            cached_time, cached_items = cls._items_cache[resource.id]
            if (now - cached_time) < cls.CACHE_TTL_SECONDS:
                if search_query:
                    q_stem = search_query.lower()[:6]
                    # ИСПРАВЛЕНО: Возвращаем ТОЛЬКО реальные совпадения (никаких cached_items[:4] при отсутствии совпадений!)
                    return [i for i in cached_items if q_stem in i["text"].lower()]
                return cached_items

        proxy_cred = await cls.get_credentials(db, "proxies")
        proxy_url = proxy_cred.api_key_or_token.strip() if (proxy_cred and proxy_cred.api_key_or_token) else ""

        ctype = resource.connector_type
        cfg = resource.connector_config or {}
        items = []

        if ctype == "hh_vacancies":
            text_filter = search_query or cfg.get("search_text", "технолог пищевого производства OR FMCG")
            status, raw_bytes = await cls.fetch_raw_capped(
                resource.url_or_id,
                params={"text": text_filter, "per_page": 6, "order_by": "publication_time"},
                use_304=(search_query is None),
                proxy_url=proxy_url
            )
            if status == 200 and raw_bytes:
                data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                items = [{
                    "author_id": v.get("employer", {}).get("name", "company"),
                    "text": f"Компания: {v.get('employer', {}).get('name')}. Вакансия: {v.get('name')}. Требования: {v.get('snippet', {}).get('requirement')}"
                } for v in data.get("items", [])[:6]]

        elif ctype == "wb_mpstats":
            cred = await cls.get_credentials(db, "wb_mpstats")
            if cred and cred.api_key_or_token:
                status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, extra_headers={"X-Mpstats-TOKEN": cred.api_key_or_token}, proxy_url=proxy_url)
                if status == 200 and raw_bytes:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                    items = [{"author_id": "wb_mpstats", "text": str(item)} for item in data[:8]]
            else:
                q = search_query or cfg.get("default_query", "напиток клетчатка протеин снек")
                wb_url = f"https://search.wb.ru/exactmatch/ru/common/v7/search?ab_testing=false&appType=1&curr=rub&dest=-1257786&query={q}&resultset=catalog&sort=Newly"
                status, raw_bytes = await cls.fetch_raw_capped(wb_url, use_304=(search_query is None), proxy_url=proxy_url)
                if status == 200 and raw_bytes:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                    items = [{
                        "author_id": p.get("brand", "seller"),
                        "text": f"Новинка WB: {p.get('name')} | Бренд: {p.get('brand')} | Отзывов: {p.get('feedbacks')}"
                    } for p in data.get("data", {}).get("products", [])[:8]]

        elif ctype == "telegram_web":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True, proxy_url=proxy_url)
            if status == 200 and raw_bytes:
                soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="ignore"), "html.parser")
                for msg in soup.select(".tgme_widget_message")[-8:]:
                    t_el = msg.select_one(".tgme_widget_message_text")
                    views_el = msg.select_one(".tgme_widget_message_views")
                    views_str = f" (Просмотров: {views_el.get_text(strip=True)})" if views_el else ""
                    if t_el:
                        items.append({
                            "author_id": msg.get("data-post", "tg"),
                            "text": f"{t_el.get_text(' ', strip=True)}{views_str}"[:500]
                        })

        elif ctype == "json_api":
            url = resource.url_or_id
            if "reddit.com" in url and "limit=" not in url:
                url = f"{url}?limit=6&raw_json=1"
            if "2ch.hk" in url:
                url = url.replace("catalog.json", "index.json").replace("threads.json", "index.json")

            status, raw_bytes = await cls.fetch_raw_capped(url, use_304=True, proxy_url=proxy_url)
            if "reddit.com" in url and status in (403, 429):
                rss_url = resource.url_or_id.split("?")[0].replace(".json", ".rss")
                r_status, r_bytes = await cls.fetch_raw_capped(rss_url, use_304=True, proxy_url=proxy_url)
                if r_status == 200 and r_bytes:
                    feed = feedparser.parse(r_bytes.decode("utf-8", errors="ignore"))
                    for e in feed.entries[:6]:
                        entry_soup = BeautifulSoup(e.get("summary", ""), "html.parser")
                        items.append({
                            "author_id": e.get("author", "redditor"),
                            "text": f"{e.get('title', '')}. {entry_soup.get_text(' ', strip=True)[:380]}"
                        })
            elif status == 200 and raw_bytes:
                try:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                except Exception:
                    data = {}

                if isinstance(data, dict) and "data" in data and "children" in data["data"]:
                    for post in data["data"]["children"][:6]:
                        pd = post.get("data", {})
                        items.append({
                            "author_id": pd.get("author", "redditor"),
                            "text": f"{pd.get('title', '')}. {pd.get('selftext', '')[:360]}"
                        })
                elif isinstance(data, dict) and "threads" in data:
                    for th in data["threads"][:8]:
                        op = th["posts"][0] if ("posts" in th and th["posts"]) else th
                        txt = BeautifulSoup(op.get("comment", ""), "html.parser").get_text(" ", strip=True)
                        files = op.get("files", [])
                        file_names = [f.get("fullname") or f.get("displayname") or "" for f in files if f.get("fullname") or f.get("displayname")]
                        media_desc = f"[Медиа: {', '.join(file_names[:2])}] " if file_names else ""
                        items.append({
                            "author_id": str(op.get("num", "anon")),
                            "text": f"{media_desc}{op.get('subject', '')}: {txt[:360]}"
                        })

        elif ctype == "rss_feed":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True, proxy_url=proxy_url)
            if status == 200 and raw_bytes:
                feed = feedparser.parse(raw_bytes.decode("utf-8", errors="ignore"))
                for entry in feed.entries[:8]:
                    s_soup = BeautifulSoup(entry.get("summary", ""), "html.parser")
                    summary = s_soup.get_text(" ", strip=True)
                    full = f"{entry.get('title', '')}. {summary}"
                    items.append({"author_id": entry.get("author", resource.name), "text": full[:480]})

        elif ctype == "html_scraper":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True, proxy_url=proxy_url)
            if status == 200 and raw_bytes:
                soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="ignore"), "html.parser")
                sel = cfg.get("item_selector", "article, .item, .news-item, .comment_text, p")
                for el in soup.select(sel)[:8]:
                    txt = el.get_text(" ", strip=True)
                    if len(txt) > 45:
                        items.append({"author_id": resource.name, "text": txt[:450]})

        if items:
            cls._items_cache[resource.id] = (now, items)

        if search_query and items:
            q_stem = search_query.lower()[:6]
            return [i for i in items if q_stem in i["text"].lower()]
        return items

import time
import json
import base64
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

    MAX_RESPONSE_BYTES = 260_000  # 260 КБ потолок на текстово-медийный поток
    MAX_IMAGE_BYTES = 90_000      # 90 КБ потолок на превью картинки/стоп-кадра видео для локальной VLM
    CACHE_TTL_SECONDS = 900

    @classmethod
    def get_client(cls, proxy_url: str = "") -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed or cls._current_proxy != proxy_url:
            cls._current_proxy = proxy_url
            kwargs = {
                "timeout": 15.0,
                "follow_redirects": True,
                "limits": httpx.Limits(max_keepalive_connections=25, max_connections=50, keepalive_expiry=60.0),
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
    async def fetch_image_as_base64(cls, image_url: str, proxy_url: str = "") -> str | None:
        """Скачивает легкое превью картинки или стоп-кадра видео (до 90 КБ) и переводит в base64 для локальной модели (Ollama/vLLM)"""
        if not image_url or not image_url.startswith("http"):
            return None
        try:
            client = cls.get_client(proxy_url)
            async with client.stream("GET", image_url) as resp:
                if resp.status_code != 200:
                    return None
                chunks = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=16384):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= cls.MAX_IMAGE_BYTES:
                        break
                raw_img = b"".join(chunks)
                b64 = base64.b64encode(raw_img).decode("ascii")
                mime = resp.headers.get("Content-Type", "image/jpeg")
                return f"data:{mime};base64,{b64}"
        except Exception:
            return None

    @classmethod
    async def fetch_raw_capped(cls, url: str, params: dict = None, extra_headers: dict = None, use_304: bool = True, proxy_url: str = "") -> tuple[int, bytes]:
        client = cls.get_client(proxy_url)
        req_headers = dict(extra_headers or {})
        if "reddit.com" in url:
            req_headers["User-Agent"] = "linux:trend.swarm.analytics:v2.2 (by /u/trend_research_bot)"

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
        if resource.id in cls._items_cache:
            cached_time, cached_items = cls._items_cache[resource.id]
            if (now - cached_time) < cls.CACHE_TTL_SECONDS:
                if search_query:
                    q_low = search_query.lower()
                    matched = [i for i in cached_items if q_low in i["text"].lower()]
                    return matched if matched else cached_items[:4]
                return cached_items

        proxy_cred = await cls.get_credentials(db, "proxies")
        proxy_url = proxy_cred.api_key_or_token.strip() if (proxy_cred and proxy_cred.api_key_or_token) else ""

        ctype = resource.connector_type
        cfg = resource.connector_config or {}
        items = []

        # 1. ВАКАНСИИ HH.RU
        if ctype == "hh_vacancies":
            text_filter = search_query or cfg.get("search_text", "технолог пищевого производства OR FMCG")
            status, raw_bytes = await cls.fetch_raw_capped(
                resource.url_or_id,
                params={"text": text_filter, "per_page": 8, "order_by": "publication_time"},
                use_304=(search_query is None),
                proxy_url=proxy_url
            )
            if status == 200 and raw_bytes:
                data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                items = [{
                    "author_id": v.get("employer", {}).get("name", "company"),
                    "text": f"Компания: {v.get('employer', {}).get('name')}. Вакансия: {v.get('name')}. Требования: {v.get('snippet', {}).get('requirement')}",
                    "image_url": None,
                    "video_url": None
                } for v in data.get("items", [])[:8]]

        # 2. WILDBERRIES / MPSTATS (Добавлено извлечение фото карточек товаров-новинок!)
        elif ctype == "wb_mpstats":
            cred = await cls.get_credentials(db, "wb_mpstats")
            if cred and cred.api_key_or_token:
                status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, extra_headers={"X-Mpstats-TOKEN": cred.api_key_or_token}, proxy_url=proxy_url)
                if status == 200 and raw_bytes:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                    items = [{"author_id": "wb_mpstats", "text": str(item), "image_url": None, "video_url": None} for item in data[:10]]
            else:
                q = search_query or cfg.get("default_query", "напиток клетчатка протеин снек")
                wb_url = f"https://search.wb.ru/exactmatch/ru/common/v7/search?ab_testing=false&appType=1&curr=rub&dest=-1257786&query={q}&resultset=catalog&sort=Newly"
                status, raw_bytes = await cls.fetch_raw_capped(wb_url, use_304=(search_query is None), proxy_url=proxy_url)
                if status == 200 and raw_bytes:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                    items = [{
                        "author_id": p.get("brand", "seller"),
                        "text": f"Новинка WB: {p.get('name')} | Бренд: {p.get('brand')} | Отзывов: {p.get('feedbacks')}",
                        "image_url": None,
                        "video_url": None
                    } for p in data.get("data", {}).get("products", [])[:10]]

        # 3. TELEGRAM (Фото + Мемы + Видеоролики + Стоп-кадры видео + Счетчик просмотров)
        elif ctype == "telegram_web":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True, proxy_url=proxy_url)
            if status == 200 and raw_bytes:
                soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="ignore"), "html.parser")
                for msg in soup.select(".tgme_widget_message")[-10:]:
                    t_el = msg.select_one(".tgme_widget_message_text")
                    p_el = msg.select_one(".tgme_widget_message_photo_wrap")
                    v_thumb = msg.select_one(".tgme_widget_message_video_thumb")
                    v_tag = msg.select_one("video")
                    views_el = msg.select_one(".tgme_widget_message_views")

                    img = None
                    for el in (p_el, v_thumb):
                        if el and "url('" in el.get("style", ""):
                            img = el["style"].split("url('")[1].split("')")[0]
                            break
                    vid = v_tag.get("src") if v_tag else None
                    views_str = f" [Просмотров: {views_el.get_text(strip=True)}]" if views_el else ""
                    media_tag = " [🎬 ВИДЕО]" if (vid or v_thumb) else (" [🖼 ФОТО/МЕМ]" if img else "")

                    if t_el or img or vid:
                        base_txt = t_el.get_text(" ", strip=True) if t_el else f"Визуальный пост в {resource.name}"
                        items.append({
                            "author_id": msg.get("data-post", "tg"),
                            "text": f"{base_txt}{media_tag}{views_str}"[:550],
                            "image_url": img,
                            "video_url": vid or (f"https://t.me/{msg.get('data-post')}" if v_thumb else None)
                        })

        # 4. ФОРУМЫ И ИМИДЖБОРДЫ (Reddit + Двач: Мемы, Картинки и Видео .mp4/.webm со стоп-кадрами!)
        elif ctype == "json_api":
            url = resource.url_or_id
            if "reddit.com" in url and "limit=" not in url:
                url = f"{url}?limit=8&raw_json=1"
            # На Дваче берем первую страницу доски index.json (весит мало, но содержит массив files с мемами и .webm/.mp4!)
            if "2ch.hk" in url:
                url = url.replace("catalog.json", "index.json").replace("threads.json", "index.json")

            status, raw_bytes = await cls.fetch_raw_capped(url, use_304=True, proxy_url=proxy_url)
            if "reddit.com" in url and status in (403, 429):
                rss_url = resource.url_or_id.split("?")[0].replace(".json", ".rss")
                r_status, r_bytes = await cls.fetch_raw_capped(rss_url, use_304=True, proxy_url=proxy_url)
                if r_status == 200 and r_bytes:
                    feed = feedparser.parse(r_bytes.decode("utf-8", errors="ignore"))
                    for e in feed.entries[:8]:
                        entry_soup = BeautifulSoup(e.get("summary", ""), "html.parser")
                        img_el = entry_soup.select_one("img")
                        items.append({
                            "author_id": e.get("author", "redditor"),
                            "text": f"{e.get('title', '')}. {entry_soup.get_text(' ', strip=True)[:400]}",
                            "image_url": img_el.get("src") if img_el else None,
                            "video_url": None
                        })
            elif status == 200 and raw_bytes:
                try:
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                except Exception:
                    data = {}

                # Разбор Reddit (включая видео Reddit, превью-кадры и мемы)
                if isinstance(data, dict) and "data" in data and "children" in data["data"]:
                    for post in data["data"]["children"][:8]:
                        pd = post.get("data", {})
                        direct_url = str(pd.get("url", ""))
                        img = direct_url if direct_url.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) else None
                        # Если это видео или галерея — берем стоп-кадр из preview
                        if not img and pd.get("preview", {}).get("images"):
                            img = pd["preview"]["images"][0].get("source", {}).get("url")
                        if not img and str(pd.get("thumbnail", "")).startswith("http"):
                            img = pd["thumbnail"]

                        vid = None
                        if pd.get("is_video") and pd.get("media", {}).get("reddit_video"):
                            vid = pd["media"]["reddit_video"].get("fallback_url")
                        elif any(v_host in direct_url for v_host in ("youtube.com", "youtu.be", "tiktok.com", "v.redd.it", ".mp4", ".webm")):
                            vid = direct_url

                        viral_stats = f" [👍 {pd.get('ups', 0)} | 💬 {pd.get('num_comments', 0)}]"
                        media_mark = " [🎬 ВИДЕО]" if vid else (" [🖼 МЕМ/ФОТО]" if img else "")
                        items.append({
                            "author_id": pd.get("author", "redditor"),
                            "text": f"{pd.get('title', '')}{media_mark}{viral_stats} — {pd.get('selftext', '')[:380]}",
                            "image_url": img,
                            "video_url": vid
                        })

                # Разбор Двача (2ch.hk index.json — мемы, картинки и видео .webm / .mp4 с легкими превью!)
                elif isinstance(data, dict) and "threads" in data:
                    for th in data["threads"][:10]:
                        op = th["posts"][0] if ("posts" in th and th["posts"]) else th
                        txt = BeautifulSoup(op.get("comment", ""), "html.parser").get_text(" ", strip=True)
                        files = op.get("files", [])
                        img, vid = None, None
                        if files:
                            f0 = files[0]
                            f_path = f0.get("path", "")
                            f_thumb = f0.get("thumbnail", "")
                            # Всегда берем легкое превью (10-15 КБ) как картинку для анализа!
                            if f_thumb:
                                img = f"https://2ch.hk{f_thumb}"
                            elif f_path.endswith((".jpg", ".png", ".webp", ".gif")):
                                img = f"https://2ch.hk{f_path}"
                            # Если прикреплен видеоролик (.webm / .mp4) — сохраняем прямую ссылку на видео!
                            if f_path.endswith((".webm", ".mp4", ".mov")):
                                vid = f"https://2ch.hk{f_path}"

                        replies_cnt = th.get("posts_count", len(th.get("posts", [])))
                        media_mark = " [🎬 ВИДЕО WEBM/MP4]" if vid else (" [🖼 МЕМ/КАРТИНКА]" if img else "")
                        items.append({
                            "author_id": str(op.get("num", "anon")),
                            "text": f"{op.get('subject', '')}{media_mark} [Ответов: {replies_cnt}]: {txt[:380]}",
                            "image_url": img,
                            "video_url": vid
                        })

        # 5. RSS / ATOM (С извлечением медиа-превью и вложений)
        elif ctype == "rss_feed":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True, proxy_url=proxy_url)
            if status == 200 and raw_bytes:
                feed = feedparser.parse(raw_bytes.decode("utf-8", errors="ignore"))
                for entry in feed.entries[:10]:
                    raw_sum = entry.get("summary", "")
                    s_soup = BeautifulSoup(raw_sum, "html.parser")
                    summary = s_soup.get_text(" ", strip=True)

                    img = None
                    if entry.get("media_thumbnail"):
                        img = entry["media_thumbnail"][0].get("url")
                    elif entry.get("media_content"):
                        img = entry["media_content"][0].get("url")
                    elif s_soup.select_one("img"):
                        img = s_soup.select_one("img").get("src")

                    full = f"{entry.get('title', '')}. {summary}"
                    items.append({
                        "author_id": entry.get("author", resource.name),
                        "text": full[:550],
                        "image_url": img if (img and str(img).startswith("http")) else None,
                        "video_url": None
                    })

        # 6. HTML-СКРЕЙПЕР (С извлечением картинок товаров/новостей и видео-тегов)
        elif ctype == "html_scraper":
            status, raw_bytes = await cls.fetch_raw_capped(resource.url_or_id, use_304=True, proxy_url=proxy_url)
            if status == 200 and raw_bytes:
                soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="ignore"), "html.parser")
                sel = cfg.get("item_selector", "article, .item, .news-item, .comment_text, p")
                for el in soup.select(sel)[:10]:
                    txt = el.get_text(" ", strip=True)
                    if len(txt) > 40:
                        img_el = el.select_one("img")
                        vid_el = el.select_one("video, iframe[src*='youtube'], iframe[src*='tiktok']")
                        img_src = img_el.get("src") or img_el.get("data-src") if img_el else None
                        if img_src and img_src.startswith("/"):
                            base_domain = "/".join(resource.url_or_id.split("/")[:3])
                            img_src = base_domain + img_src
                        items.append({
                            "author_id": resource.name,
                            "text": txt[:500],
                            "image_url": img_src if (img_src and img_src.startswith("http")) else None,
                            "video_url": vid_el.get("src") if vid_el else None
                        })

        if items:
            cls._items_cache[resource.id] = (now, items)

        if search_query and items:
            q_low = search_query.lower()
            matched = [i for i in items if q_low in i["text"].lower()]
            return matched
        return items

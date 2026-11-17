import httpx
import feedparser
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Resource, ConnectorCredential

class ResourceConnectorEngine:
    @staticmethod
    async def get_credentials(db: AsyncSession, service_name: str) -> ConnectorCredential | None:
        res = await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == service_name))
        return res.scalars().first()

    @classmethod
    async def fetch(cls, db: AsyncSession, resource: Resource, search_query: str = None) -> list[dict]:
        ctype = resource.connector_type
        cfg = resource.connector_config or {}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"}

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            if ctype == "hh_vacancies":
                text_filter = search_query or cfg.get("search_text", "технолог пищевого производства OR FMCG")
                resp = await client.get(resource.url_or_id, params={"text": text_filter, "per_page": 15, "order_by": "publication_time"})
                return [{
                    "author_id": v.get("employer", {}).get("name", "company"),
                    "text": f"Компания: {v.get('employer', {}).get('name')}. Вакансия: {v.get('name')}. Требования: {v.get('snippet', {}).get('requirement')}",
                    "image_url": None
                } for v in resp.json().get("items", [])]

            elif ctype == "wb_mpstats":
                cred = await cls.get_credentials(db, "wb_mpstats")
                if cred and cred.api_key_or_token:
                    resp = await client.get(resource.url_or_id, headers={"X-Mpstats-TOKEN": cred.api_key_or_token})
                    if resp.status_code == 200:
                        return [{"author_id": "wb_mpstats", "text": str(item), "image_url": None} for item in resp.json()[:15]]
                q = search_query or cfg.get("default_query", "напиток клетчатка протеин снек")
                wb_url = f"https://search.wb.ru/exactmatch/ru/common/v7/search?ab_testing=false&appType=1&curr=rub&dest=-1257786&query={q}&resultset=catalog&sort=Newly"
                resp = await client.get(wb_url)
                if resp.status_code == 200:
                    return [{
                        "author_id": p.get("brand", "seller"),
                        "text": f"Новинка WB: {p.get('name')} | Бренд: {p.get('brand')} | Отзывов: {p.get('feedbacks')}",
                        "image_url": None
                    } for p in resp.json().get("data", {}).get("products", [])[:15]]
                return []

            elif ctype == "telegram_web":
                url = f"{resource.url_or_id}?q={search_query}" if search_query else resource.url_or_id
                resp = await client.get(url)
                soup = BeautifulSoup(resp.text, "html.parser")
                items = []
                for msg in soup.select(".tgme_widget_message")[-15:]:
                    t_el = msg.select_one(".tgme_widget_message_text")
                    p_el = msg.select_one(".tgme_widget_message_photo_wrap")
                    img = p_el["style"].split("url('")[1].split("')")[0] if (p_el and "url('" in p_el.get("style", "")) else None
                    if t_el or img:
                        items.append({"author_id": msg.get("data-post", "tg"), "text": t_el.get_text(" ", strip=True) if t_el else "[Мем/Фото]", "image_url": img})
                return items

            elif ctype == "json_api":
                resp = await client.get(resource.url_or_id)
                data = resp.json()
                items = []
                if isinstance(data, dict) and "data" in data and "children" in data["data"]:
                    for post in data["data"]["children"][:15]:
                        pd = post.get("data", {})
                        img = pd.get("url") if str(pd.get("url", "")).endswith((".jpg", ".png", ".webp")) else None
                        items.append({"author_id": pd.get("author", "redditor"), "text": f"{pd.get('title', '')} - {pd.get('selftext', '')[:500]}", "image_url": img})
                elif isinstance(data, dict) and "threads" in data:
                    for th in data["threads"][:15]:
                        op = th["posts"][0] if "posts" in th else th
                        txt = BeautifulSoup(op.get("comment", ""), "html.parser").get_text(" ", strip=True)
                        files = op.get("files", [])
                        img = f"https://2ch.hk{files[0]['path']}" if files else None
                        items.append({"author_id": str(op.get("num", "anon")), "text": f"{op.get('subject', '')}: {txt[:500]}", "image_url": img})
                return items

            elif ctype == "rss_feed":
                resp = await client.get(resource.url_or_id)
                feed = feedparser.parse(resp.text)
                items = []
                for entry in feed.entries[:15]:
                    summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
                    full = f"{entry.get('title', '')}. {summary}"
                    if search_query and search_query.lower() not in full.lower():
                        continue
                    items.append({"author_id": entry.get("author", resource.name), "text": full[:700], "image_url": None})
                return items

            elif ctype == "html_scraper":
                resp = await client.get(resource.url_or_id)
                soup = BeautifulSoup(resp.text, "html.parser")
                sel = cfg.get("item_selector", "article, .item, .news-item, .comment_text, p")
                return [{"author_id": resource.name, "text": el.get_text(" ", strip=True)[:650], "image_url": None} for el in soup.select(sel)[:15] if len(el.get_text(strip=True)) > 40]
        return []

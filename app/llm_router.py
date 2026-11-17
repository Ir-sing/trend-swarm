import os
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from litellm import acompletion
from app.models import AgentRoleConfig, ConnectorCredential
from app.math_engine import TrendMathEngine

class DynamicModelRouter:
    @staticmethod
    async def ask_agent(db: AsyncSession, role_name: str, user_payload: str, image_url: str = None) -> dict:
        cfg = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == role_name))).scalars().first()
        if not cfg:
            return {"is_anomaly": False}

        cred = (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == cfg.credential_service))).scalars().first()
        api_key = (cred.api_key_or_token if cred and cred.api_key_or_token else None) or os.getenv("OPENROUTER_API_KEY")

        if api_key or cfg.provider in ("ollama", "vllm"):
            try:
                messages = [{"role": "system", "content": cfg.system_prompt}]
                if image_url:
                    messages.append({"role": "user", "content": [{"type": "text", "text": user_payload}, {"type": "image_url", "image_url": {"url": image_url}}]})
                else:
                    messages.append({"role": "user", "content": user_payload})

                kwargs = {
                    "model": f"{cfg.provider}/{cfg.model_name}" if cfg.provider not in cfg.model_name else cfg.model_name,
                    "messages": messages,
                    "temperature": cfg.temperature,
                    "response_format": {"type": "json_object"}
                }
                if cfg.api_base:
                    kwargs["api_base"] = cfg.api_base
                if api_key:
                    kwargs["api_key"] = api_key

                resp = await acompletion(**kwargs)
                return json.loads(resp.choices[0].message.content)
            except Exception as e:
                print(f"⚠️ LLM fallback ({cfg.model_name}): {e}")

        text_lower = user_payload.lower()
        triggers = ["клетчатк", "fiber", "протеин", "protein", "завод", "инвест", "фермент", "gut", "новинк", "состав", "технолог", "рецептур", "дефицит", "сахар", "prebiotic", "glp"]
        matched = [t for t in triggers if t in text_lower]
        if matched:
            return {
                "is_anomaly": True,
                "confirmed": True,
                "title": f"Сигнал [{', '.join(matched[:3])}]",
                "essence": f"Зафиксирован всплеск по маркерам ({', '.join(matched)}): {user_payload[:260]}...",
                "core_essence": f"Сдвиг спроса/инвестиций по маркерам ({', '.join(matched)}): {user_payload[:320]}",
                "delta_summary": f"Новое подтверждение ({', '.join(matched)}).",
                "summary": user_payload[:220],
                "multilang_keywords": matched[:4]
            }
        return {"is_anomaly": False, "confirmed": False}

    @staticmethod
    def compute_fast_vector(text: str) -> list[float]:
        return TrendMathEngine.compute_multilang_vector(text)

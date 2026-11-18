import os
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from litellm import acompletion
from app.models import AgentRoleConfig, ConnectorCredential
from app.math_engine import TrendMathEngine

class DynamicModelRouter:
    @classmethod
    async def ask_agent(cls, db: AsyncSession, role_name: str, user_payload: str, raw_items: list[dict] = None, image_url: str = None) -> dict:
        # ШАГ 1: Математический пред-фильтр ДО вызова нейросети (0 токенов!)
        # Если мы проверяем сырые посты с сайта и в них нет ни одного маркера сдвига поведения/инвестиций —
        # мы НЕ вызываем модель вообще (экономия 90% токенов и времени GPU/CPU)
        extracted = None
        if raw_items is not None:
            extracted = TrendMathEngine.extract_clean_facts_and_terms(raw_items)
            if not extracted.get("has_signal"):
                return {"is_anomaly": False, "confirmed": False}
            # Вместо простыни на 2500 символов передаем в модель только выжатый релевантный пост (до 360 символов!)
            user_payload = extracted.get("compact_payload", user_payload[:600])

        cfg = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == role_name))).scalars().first()
        if not cfg:
            return {"is_anomaly": False, "confirmed": False}

        cred = (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == cfg.credential_service))).scalars().first()
        token_or_url = cred.api_key_or_token.strip() if (cred and cred.api_key_or_token) else ""

        api_base = cfg.api_base or os.getenv("LOCAL_LLM_URL")
        api_key = None

        if token_or_url.startswith("http://") or token_or_url.startswith("https://"):
            api_base = token_or_url
        elif token_or_url:
            api_key = token_or_url

        # ШАГ 2: Вызов локальной модели (Ollama / vLLM / Hermes) или внешнего API на сжатом контексте (~150-200 токенов)
        if cfg.provider in ("ollama", "vllm", "openai_compatible") or api_key or api_base:
            try:
                messages = [
                    {"role": "system", "content": cfg.system_prompt},
                    {"role": "user", "content": user_payload[:750]}
                ]
                model_str = cfg.model_name
                if cfg.provider not in model_str and cfg.provider in ("ollama", "openrouter", "openai"):
                    model_str = f"{cfg.provider}/{model_str}"

                kwargs = {
                    "model": model_str,
                    "messages": messages,
                    "temperature": cfg.temperature,
                    "max_tokens": 220,  # Ограничиваем длину ответа, чтобы модель не лила воду
                    "response_format": {"type": "json_object"},
                    "timeout": 20.0
                }
                if api_base:
                    kwargs["api_base"] = api_base
                elif cfg.provider == "ollama":
                    kwargs["api_base"] = "http://host.docker.internal:11434"
                if api_key:
                    kwargs["api_key"] = api_key

                resp = await acompletion(**kwargs)
                parsed = json.loads(resp.choices[0].message.content)
                if isinstance(parsed, dict) and parsed.get("title"):
                    return parsed
            except Exception:
                pass

        # ШАГ 3: Если локальная модель сейчас не запущена — возвращаем чистый результат математического экстрактора
        if extracted and extracted.get("has_signal"):
            return {
                "is_anomaly": True,
                "confirmed": True,
                "title": extracted["title"],
                "essence": extracted["essence"],
                "core_essence": extracted["essence"],
                "delta_summary": extracted["essence"],
                "summary": extracted["essence"],
                "multilang_keywords": extracted["keywords"]
            }

        return {"is_anomaly": False, "confirmed": False}

    @staticmethod
    def compute_fast_vector(text: str) -> list[float]:
        return TrendMathEngine.compute_multilang_vector(text)

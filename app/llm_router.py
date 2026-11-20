import os
import re
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from litellm import acompletion, aembedding
from app.models import AgentRoleConfig, ConnectorCredential
from app.math_engine import TrendMathEngine


def safe_parse_llm_json(raw_str: str) -> dict:
    if not raw_str:
        return {}
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_str.strip(), flags=re.IGNORECASE | re.MULTILINE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class DynamicModelRouter:
    @staticmethod
    async def _resolve_connection(db: AsyncSession, cfg: AgentRoleConfig) -> tuple[str | None, str | None]:
        cred = (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == cfg.credential_service))).scalars().first()
        token_or_url = cred.api_key_or_token.strip() if (cred and cred.api_key_or_token) else ""

        api_base = cfg.api_base or os.getenv("LOCAL_LLM_URL")
        api_key = None

        if token_or_url.startswith("http://") or token_or_url.startswith("https://"):
            api_base = token_or_url
        elif token_or_url:
            api_key = token_or_url

        if not api_base and cfg.provider == "ollama":
            api_base = "http://host.docker.internal:11434"
        return api_base, api_key

    @classmethod
    async def get_semantic_embedding(cls, db: AsyncSession, text: str) -> list[float]:
        """
        Получает плотный 1024-мерный мультиязычный вектор (bge-m3 через настраиваемую роль embedder).
        Если локальный нейро-эмбеддер недоступен — строит морфологический 3/4-граммный вектор (без потери размерности).
        """
        clean_txt = (text or "").strip()[:1200]
        if not clean_txt:
            return [0.0] * 1024

        cfg = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == "embedder"))).scalars().first()
        if cfg:
            api_base, api_key = await cls._resolve_connection(db, cfg)
            if cfg.provider in ("ollama", "vllm", "openai_compatible") or api_key:
                try:
                    model_str = cfg.model_name
                    if cfg.provider not in model_str and cfg.provider in ("ollama", "openrouter", "openai"):
                        model_str = f"{cfg.provider}/{model_str}"
                    kwargs = {"model": model_str, "input": [clean_txt], "timeout": 12.0}
                    if api_base:
                        kwargs["api_base"] = api_base
                    if api_key:
                        kwargs["api_key"] = api_key
                    resp = await aembedding(**kwargs)
                    vec = resp.data[0]["embedding"]
                    if len(vec) == 1024:
                        return vec
                    elif len(vec) < 1024:
                        return vec + [0.0] * (1024 - len(vec))
                    else:
                        return vec[:1024]
                except Exception:
                    pass

        return TrendMathEngine.compute_char_ngram_vector(clean_txt)

    @classmethod
    async def ask_agent(
        cls,
        db: AsyncSession,
        role_name: str,
        user_payload: str
    ) -> dict:
        """
        Отправляет текст напрямую нейросети заданной роли (без отсечения по отдельным словам!).
        Принимает любой валидный JSON-ответ (включая решения Королевы {"boost_block": ...}).
        """
        cfg = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == role_name))).scalars().first()
        if not cfg:
            return {"llm_online": False}

        api_base, api_key = await cls._resolve_connection(db, cfg)
        if cfg.provider not in ("ollama", "vllm", "openai_compatible") and not api_key:
            return {"llm_online": False}

        try:
            messages = [
                {"role": "system", "content": cfg.system_prompt},
                {"role": "user", "content": user_payload[:2200]}
            ]
            model_str = cfg.model_name
            if cfg.provider not in model_str and cfg.provider in ("ollama", "openrouter", "openai"):
                model_str = f"{cfg.provider}/{model_str}"

            kwargs = {
                "model": model_str,
                "messages": messages,
                "temperature": cfg.temperature,
                "max_tokens": 320,
                "response_format": {"type": "json_object"},
                "timeout": 30.0
            }
            if api_base:
                kwargs["api_base"] = api_base
            if api_key:
                kwargs["api_key"] = api_key

            resp = await acompletion(**kwargs)
            parsed = safe_parse_llm_json(resp.choices[0].message.content)
            if parsed:
                parsed["llm_online"] = True
                return parsed
        except Exception as e:
            return {"llm_online": False, "error": str(e)[:120]}

        return {"llm_online": False}

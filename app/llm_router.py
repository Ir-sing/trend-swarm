import os
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from litellm import acompletion
from app.models import AgentRoleConfig, ConnectorCredential
from app.math_engine import TrendMathEngine
from app.connectors import ResourceConnectorEngine

class DynamicModelRouter:
    @classmethod
    async def ask_agent(cls, db: AsyncSession, role_name: str, user_payload: str, raw_items: list[dict] = None, image_url: str = None) -> dict:
        cfg = (await db.execute(select(AgentRoleConfig).where(AgentRoleConfig.role_name == role_name))).scalars().first()
        if not cfg:
            return {"is_anomaly": False}

        cred = (await db.execute(select(ConnectorCredential).where(ConnectorCredential.service_name == cfg.credential_service))).scalars().first()
        token_or_url = cred.api_key_or_token.strip() if (cred and cred.api_key_or_token) else ""

        api_base = cfg.api_base or os.getenv("LOCAL_LLM_URL")
        api_key = None

        if token_or_url.startswith("http://") or token_or_url.startswith("https://"):
            api_base = token_or_url
        elif token_or_url:
            api_key = token_or_url

        if cfg.provider in ("ollama", "vllm", "openai_compatible") or api_key or api_base:
            try:
                messages = [{"role": "system", "content": cfg.system_prompt}]
                if image_url:
                    # Для локальных моделей (Ollama llava / qwen2.5-vl) переводим превью картинки/видео в base64!
                    img_payload = image_url
                    if cfg.provider in ("ollama", "vllm"):
                        b64_img = await ResourceConnectorEngine.fetch_image_as_base64(image_url)
                        if b64_img:
                            img_payload = b64_img
                    messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_payload},
                            {"type": "image_url", "image_url": {"url": img_payload}}
                        ]
                    })
                else:
                    messages.append({"role": "user", "content": user_payload})

                model_str = cfg.model_name
                if cfg.provider not in model_str and cfg.provider in ("ollama", "openrouter", "openai"):
                    model_str = f"{cfg.provider}/{cfg.model_name}"

                kwargs = {
                    "model": model_str,
                    "messages": messages,
                    "temperature": cfg.temperature,
                    "response_format": {"type": "json_object"},
                    "timeout": 25.0
                }
                if api_base:
                    kwargs["api_base"] = api_base
                elif cfg.provider == "ollama":
                    kwargs["api_base"] = "http://host.docker.internal:11434"
                if api_key:
                    kwargs["api_key"] = api_key

                resp = await acompletion(**kwargs)
                return json.loads(resp.choices[0].message.content)
            except Exception:
                pass

        # Математический экстрактор (сохраняет найденные картинки, мемы и видео даже без запущенной VLM)
        if raw_items:
            extracted = TrendMathEngine.extract_clean_facts_and_terms(raw_items)
            if extracted.get("has_signal"):
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

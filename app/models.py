from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class ConnectorCredential(Base):
    __tablename__ = "connector_credentials"
    service_name = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    api_key_or_token = Column(Text, default="")
    extra_headers = Column(JSON, default={})
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class AgentTelemetry(Base):
    __tablename__ = "agent_telemetry"
    agent_id = Column(String, primary_key=True)
    agent_type = Column(String, default="РАЗВЕДЧИЦА")
    state = Column(String, default="STOPPED")
    current_resource = Column(String, default="-")
    current_action = Column(Text, default="Ожидание запуска")
    items_processed = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class AgentRoleConfig(Base):
    __tablename__ = "agent_role_configs"
    role_name = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    model_name = Column(String, nullable=False)
    api_base = Column(String, nullable=True)
    credential_service = Column(String, default="llm_local")
    temperature = Column(Float, default=0.2)
    system_prompt = Column(Text, nullable=False)

class SemanticBlock(Base):
    __tablename__ = "semantic_blocks"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    block_type = Column(String, nullable=False)
    description = Column(Text)
    resources = relationship("Resource", back_populates="block")

class Resource(Base):
    __tablename__ = "resources"
    id = Column(Integer, primary_key=True)
    block_id = Column(Integer, ForeignKey("semantic_blocks.id"), nullable=False)
    name = Column(String, nullable=False)
    url_or_id = Column(String, nullable=False)
    connector_type = Column(String, nullable=False)
    connector_config = Column(JSON, default={})
    geo_region = Column(String, default="RU")
    tags = Column(JSON, default=[])
    is_active = Column(Boolean, default=True)
    temperature = Column(Float, default=60.0)
    locked_by_agent = Column(String, nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    last_crawled_at = Column(DateTime(timezone=True), nullable=True)
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    last_content_hash = Column(String, nullable=True)
    health_status = Column(String, default="HEALTHY")
    fail_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    block = relationship("SemanticBlock", back_populates="resources")

class SwarmBlackboard(Base):
    __tablename__ = "swarm_blackboard"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    source_resource_name = Column(String)
    signal_summary = Column(Text, nullable=False)
    search_queries = Column(JSON, nullable=False)
    embedding = Column(Vector(1024), nullable=True)
    target_block_types = Column(JSON, nullable=False)
    checked_by_blocks = Column(JSON, default=[])
    confirmations = Column(JSON, default=[])
    priority_boost = Column(Float, default=1.0)
    status = Column(String, default="ACTIVE")

class TrendMemory(Base):
    __tablename__ = "trend_memory"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    core_essence = Column(Text, nullable=False)
    keywords_multilang = Column(JSON, default=[])
    embedding = Column(Vector(1024), nullable=True)
    lifecycle_status = Column(String, default="CALIBRATING_BASELINE")
    first_seen_at = Column(DateTime(timezone=True), default=utcnow)
    last_updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_analyzed_at = Column(DateTime(timezone=True), nullable=True)
    origin_geo = Column(String, default="GLOBAL")
    geo_spread = Column(JSON, default=[])
    unique_authors_count = Column(Integer, default=1)
    velocity = Column(Float, default=0.0)
    z_score = Column(Float, default=0.0)
    acceleration_rate = Column(Float, default=0.0)
    is_in_mainstream_media = Column(Boolean, default=False)
    delta_history = Column(JSON, default=[])

class MetaInsight(Base):
    __tablename__ = "meta_insights"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    title = Column(String, nullable=False)
    linked_trend_codes = Column(JSON, nullable=False)
    pattern_type = Column(String, default="CROSS_INDUSTRY")
    consumer_pain_summary = Column(Text, nullable=False)
    investment_or_product_thesis = Column(Text, nullable=False)
    confidence_score = Column(Float, default=0.75)

# 8. ЖУРНАЛ ДЕЙСТВИЙ ПОЛЬЗОВАТЕЛЕЙ И ВНЕШНИХ СИСТЕМ (Audit Log)
class UserAuditLog(Base):
    __tablename__ = "user_audit_logs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    actor = Column(String, nullable=False)          # IP пользователя или "Hermes-Telegram-Bot"
    action_type = Column(String, nullable=False)    # "SWARM_TOGGLE", "SCALE_BEES", "MODEL_EDIT", "HERMES_RAG_PULL" и т.д.
    target = Column(String, nullable=False)         # Какой объект изменен
    details = Column(Text, nullable=False)          # Подробности действия

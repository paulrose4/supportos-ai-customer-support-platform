from pydantic import BaseModel, ConfigDict, Field


class RecommendedProductResponse(BaseModel):
    sku: str
    name: str
    price: str | None = None
    currency: str | None = None
    source_url: str | None = None
    material: str | None = None
    weight: str | None = None
    dimensions: dict[str, str] | None = None
    match_reasons: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=10_000)
    page_path: str = Field(default="/", min_length=1, max_length=500)


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    kind: str
    risk_level: int
    trace_id: str
    handoff_id: str | None = None
    citations: list[str] = Field(default_factory=list)
    related_links: list[str] = Field(default_factory=list)
    recommended_products: list[RecommendedProductResponse] = Field(default_factory=list)


class WidgetMessagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=100)

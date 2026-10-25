from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, Field, field_validator, model_validator

GROUPS = ["基础品类", "特色服务", "消费场景", "区位与履约"]


class Profile(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    city: str = Field(min_length=2, max_length=50)
    category: str = Field(min_length=2, max_length=80)
    district: str = Field(default="", max_length=80)
    address: str = Field(default="", max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=12)
    price: str = Field(default="", max_length=80)
    service_modes: list[str] = Field(default_factory=list, max_length=8)
    website: str = Field(default="", max_length=500)

    @field_validator("name", "city", "category", "district", "address", "price", "website", mode="before")
    @classmethod
    def trim(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("website")
    @classmethod
    def website_url(cls, v):
        if v and (urlparse(v).scheme not in ("http", "https") or not urlparse(v).hostname):
            raise ValueError("官网必须是 http 或 https 链接")
        return v

    @field_validator("aliases", "tags", "service_modes")
    @classmethod
    def trim_list(cls, v):
        values = list(dict.fromkeys(x.strip() for x in v if x.strip()))
        if any(len(x) < 2 or len(x) > 80 for x in values):
            raise ValueError("每个标签或别名需为 2–80 个字符")
        return values

    @property
    def scope(self):
        from .locations import has_store_address
        return "store" if has_store_address(self) else "city_brand"


class Scenario(BaseModel):
    id: str = Field(pattern=r"^s\d{2}$")
    group: Literal["基础品类", "特色服务", "消费场景", "区位与履约"]
    question: str = Field(min_length=5, max_length=240)
    query: str = Field(min_length=4, max_length=200)
    intent: str = Field(min_length=2, max_length=100)
    used_fields: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    version: int = 1
    scenarios: list[Scenario] = Field(min_length=24, max_length=24)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_groups(self):
        if len({s.id for s in self.scenarios}) != 24:
            raise ValueError("场景 ID 不能重复")
        for group in GROUPS:
            if sum(s.group == group for s in self.scenarios) != 6:
                raise ValueError(f"{group} 必须包含 6 个场景")
        return self


class PlanEdit(BaseModel):
    expected_version: int
    profile: Profile
    plan: Plan
    accepted_suggestions: list[int] = Field(default_factory=list)


class RunCreate(BaseModel):
    plan_version: int


class Citation(BaseModel):
    evidence_id: str
    quote: str = Field(min_length=2)


class Suggestion(BaseModel):
    field: Literal["district", "address", "aliases", "tags", "price", "service_modes", "website"]
    value: str
    status: Literal["supported", "conflict", "uncertain"]
    reason: str
    citations: list[Citation] = Field(min_length=1)


class Enrichment(BaseModel):
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=16)


class Entity(BaseModel):
    name: str
    status: Literal["confirmed", "suspected", "other_store", "rejected"]
    is_target: bool
    city: str = ""
    category: str = ""
    address: str = ""
    reason: str
    citations: list[Citation] = Field(min_length=1)


class EntityBatch(BaseModel):
    entities: list[Entity] = Field(default_factory=list, max_length=30)


class Recommendation(BaseModel):
    name: str = Field(min_length=2)
    reason: str
    citations: list[Citation] = Field(min_length=1)


class Recommendations(BaseModel):
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=3)
    abstention_reason: str = ""


class ReviewCheck(BaseModel):
    name: str
    supported: bool
    reason: str
    citations: list[Citation] = Field(default_factory=list)


class RecommendationReview(BaseModel):
    checks: list[ReviewCheck] = Field(max_length=3)


class Advice(BaseModel):
    observation: str
    action: str
    retest: str
    citations: list[Citation] = Field(min_length=1)


class AdviceBatch(BaseModel):
    advice: list[Advice] = Field(default_factory=list, max_length=4)
    limitations: list[str] = Field(default_factory=list)

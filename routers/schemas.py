# -*- coding: utf-8 -*-
"""Esquemas de entrada Pydantic compartidos por los routers de OSINT-Auto."""
from typing import Optional
from pydantic import BaseModel, Field


class UsernameRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    country: Optional[str] = None
    top: Optional[int] = Field(default=200, ge=20, le=1000)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


class SocialRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    country: Optional[str] = None
    max_enrich: int = Field(default=10, ge=0, le=25)
    fetch_public_meta: bool = True
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


class NameLocationRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    city: Optional[str] = None
    country: Optional[str] = None
    keyword: Optional[str] = None
    platforms: Optional[list] = None
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


class EmailRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=160)
    # lookup = correo conocido rápido | osint = correo conocido + derivados | generate = desde username
    mode: str = Field(default="lookup", pattern="^(lookup|osint|generate)$")
    limit: int = Field(default=8, ge=1, le=20)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


class PhoneRequest(BaseModel):
    phone: str = Field(..., min_length=5, max_length=40)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


class FullRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    country: Optional[str] = None
    known_email: Optional[str] = None
    phone: Optional[str] = None
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


class BreachEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=160)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


class BreachPasswordRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class CaseCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=140)
    subject_name: Optional[str] = Field(default="", max_length=140)
    purpose: str = Field(default="autodiagnostico", max_length=40)
    notes: Optional[str] = Field(default="", max_length=2000)
    seed_username: Optional[str] = Field(default="", max_length=80)
    seed_email: Optional[str] = Field(default="", max_length=160)
    seed_phone: Optional[str] = Field(default="", max_length=40)
    seed_location: Optional[str] = Field(default="", max_length=120)
    seed_domain: Optional[str] = Field(default="", max_length=120)


class CaseUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=140)
    subject_name: Optional[str] = Field(default=None, max_length=140)
    purpose: Optional[str] = Field(default=None, max_length=40)
    notes: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = Field(default=None, pattern="^(open|closed)$")
    friend_scope_enabled: Optional[bool] = None


class IdentifierCreateRequest(BaseModel):
    kind: str = Field(..., pattern="^(username|email|phone|full_name|location|domain|other)$")
    value: str = Field(..., min_length=1, max_length=200)
    source: str = Field(default="manual", max_length=60)
    confidence: str = Field(default="ALTA", pattern="^(ALTA|MEDIA|BAJA)$")


class ProfileCreateRequest(BaseModel):
    site: str = Field(..., min_length=1, max_length=80)
    url: str = Field(..., min_length=4, max_length=500)
    username: Optional[str] = Field(default="", max_length=80)
    display_name: Optional[str] = Field(default="", max_length=160)
    bio: Optional[str] = Field(default="", max_length=600)
    location: Optional[str] = Field(default="", max_length=160)
    avatar_url: Optional[str] = Field(default="", max_length=500)
    website: Optional[str] = Field(default="", max_length=300)
    sources: str = Field(default="manual", max_length=80)
    osint_confidence: str = Field(default="ALTA", pattern="^(ALTA|MEDIA|BAJA)$")


class ProfileStatusPatchRequest(BaseModel):
    manual_status: str = Field(..., pattern="^(auto|confirmed|rejected)$")


class ConnectorIngestRequest(BaseModel):
    platform: str = Field(..., pattern="^(bluesky|mastodon|reddit|github|youtube)$")
    handle: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=25, ge=1, le=50)
    include_replies: bool = True
    confirm_legitimate: bool = True


class VisionAnalyzeRequest(BaseModel):
    provider: str = Field(default="auto", pattern="^(auto|local_context|ollama|gemini|openai)$")
    only_pending: bool = False
    max_images: int = Field(default=30, ge=1, le=100)


class NlpAnalyzeRequest(BaseModel):
    provider: str = Field(default="local", pattern="^(local|auto|ollama|gemini|openai)$")


class CaseReportRequest(BaseModel):
    anonymize_third_parties: bool = True
    include_quotes: bool = True
    include_gallery: bool = True
    reveal_confirmed_friends: bool = False


class CleanupRequest(BaseModel):
    dry_run: bool = True


class ManualInteractionRequest(BaseModel):
    peer_handle: str = Field(..., min_length=1, max_length=100)
    direction: str = Field(default="incoming", pattern="^(incoming|outgoing|mutual)$")
    kind: str = Field(default="comment", pattern="^(comment|reply|mention|collab|friend)$")
    platform: str = Field(default="facebook", max_length=40)
    content: Optional[str] = Field(default="", max_length=1000)
    published_at: Optional[str] = Field(default="", max_length=40)


class AccountInvestigateRequest(BaseModel):
    platform: str = Field(..., pattern="^(bluesky|mastodon|reddit|github|youtube|instagram_pro|facebook_page)$")
    handle: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=25, ge=1, le=50)
    include_replies: bool = True
    include_reactions: bool = True
    connection_id: Optional[str] = None
    run_relational: bool = True
    relational_provider: str = Field(default="local", pattern="^(local|auto|gemini|ollama|openai)$")
    run_nlp: bool = True
    nlp_provider: str = Field(default="local", pattern="^(local|auto|gemini|ollama|openai)$")
    run_sna: bool = True
    confirm_legitimate: bool = True


class RelationalAnalyzeRequest(BaseModel):
    provider: str = Field(default="local", pattern="^(local|auto|gemini|ollama|openai)$")


class RelationalReviewPatchRequest(BaseModel):
    review_status: str = Field(..., pattern="^(pending|validated|ambiguous|dismissed)$")
    category: Optional[str] = Field(default=None, pattern="^(familia|amistad|pareja|laboral_academico|afectivo_general)$")
    reviewer_note: Optional[str] = Field(default=None, max_length=300)


class BrowserSessionOpenRequest(BaseModel):
    platform: str = Field(..., pattern="^(instagram|facebook)$")


class BrowserCacheCleanRequest(BaseModel):
    platform: Optional[str] = Field(default=None, pattern="^(instagram|facebook)?$")
    dry_run: bool = False


class BrowserAgentStartRequest(BaseModel):
    platform: str = Field(..., pattern="^(instagram|facebook)$")
    target_url: str = Field(..., min_length=2, max_length=400)
    max_posts: int = Field(default=12, ge=1, le=40)
    max_comments_per_post: int = Field(default=15, ge=1, le=40)
    delay_seconds: float = Field(default=2.0, ge=1.0, le=8.0)
    confirm_legitimate: bool = True


class StagedItemPatchRequest(BaseModel):
    selected: bool


class AgentCommitRequest(BaseModel):
    run_relational: bool = True
    run_nlp: bool = True
    run_sna: bool = True


class FriendScanStartRequest(BaseModel):
    authorized: bool = False
    parent_run_id: str = ""
    platform: str = "instagram"
    delay_seconds: Optional[float] = None
    headless: bool = False


class GeoHintsRecomputeRequest(BaseModel):
    use_nominatim: Optional[bool] = None


class MediaGeoInvestigateRequest(BaseModel):
    manual_override: bool = False
    country: str = ""
    region: str = ""
    city: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    evidence_quote: str = ""
    use_nominatim: Optional[bool] = None
    use_vlm: bool = True


class FriendAddRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=500)
    platform: str = "instagram"
    label: str = ""


class FriendInvestigateRequest(BaseModel):
    compare_with_subject: bool = False
    authorized: bool = True
    use_vlm: bool = False


class FriendLinkSnaRequest(BaseModel):
    confirm: bool = False
    visual_proximity_score: float = 0.0
    matches_count: int = 1


class SubjectProfileReviewRequest(BaseModel):
    human_review: str = Field(..., pattern="^(pending|validated|ambiguous|discarded)$")

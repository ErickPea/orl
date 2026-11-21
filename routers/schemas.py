# -*- coding: utf-8 -*-
"""Esquemas de entrada Pydantic compartidos por los routers de OSINT-Auto."""
from typing import Optional
from pydantic import BaseModel, Field


# Para qué sirve: Valida la entrada del endpoint de búsqueda rápida por nombre de usuario.
# Cómo funciona: Exige `username` acotado, acepta filtro opcional de país, límite `top` y caso destino.
class UsernameRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    country: Optional[str] = None
    top: Optional[int] = Field(default=200, ge=20, le=1000)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


# Para qué sirve: Valida la petición de búsqueda y enriquecimiento de perfiles sociales por alias.
# Cómo funciona: Limita `max_enrich` (0-25) y controla si se extraen metadatos públicos HTML y el `case_id`.
class SocialRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    country: Optional[str] = None
    max_enrich: int = Field(default=10, ge=0, le=25)
    fetch_public_meta: bool = True
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


# Para qué sirve: Valida la búsqueda de personas por nombre completo y contexto geográfico.
# Cómo funciona: Requiere `full_name` y admite ciudad, país, palabra clave, lista de plataformas y caso asociado.
class NameLocationRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    city: Optional[str] = None
    country: Optional[str] = None
    keyword: Optional[str] = None
    platforms: Optional[list] = None
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


# Para qué sirve: Valida consultas OSINT de correo electrónico o derivación de posibles direcciones.
# Cómo funciona: Restringe `mode` a `lookup|osint|generate` mediante regex y acota `limit` (1-20).
class EmailRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=160)
    # lookup = correo conocido rápido | osint = correo conocido + derivados | generate = desde username
    mode: str = Field(default="lookup", pattern="^(lookup|osint|generate)$")
    limit: int = Field(default=8, ge=1, le=20)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


# Para qué sirve: Valida la petición de análisis OSINT de un número telefónico.
# Cómo funciona: Exige `phone` (5-40 caracteres) junto con `case_id` y confirmación de uso legítimo.
class PhoneRequest(BaseModel):
    phone: str = Field(..., min_length=5, max_length=40)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


# Para qué sirve: Valida la ejecución del pipeline OSINT completo multi-vector.
# Cómo funciona: Toma `username` obligatorio y combina opcionalmente país, correo conocido, teléfono y `case_id`.
class FullRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    country: Optional[str] = None
    known_email: Optional[str] = None
    phone: Optional[str] = None
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


# Para qué sirve: Valida la verificación de filtraciones asociadas a un correo electrónico.
# Cómo funciona: Exige `email` (mínimo 3 caracteres) y banderas opcionales de caso y legitimidad.
class BreachEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=160)
    case_id: Optional[str] = None
    confirm_legitimate: bool = True


# Para qué sirve: Valida la entrada para comprobar exposición de contraseña mediante k-anonimato.
# Cómo funciona: Acepta `password` de 1 a 256 caracteres sin exigir caso ni datos adicionales.
class BreachPasswordRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


# Para qué sirve: Valida la creación de un nuevo caso OSINT con sus identificadores semilla.
# Cómo funciona: Exige `title` y admite nombre del sujeto, propósito, notas y semillas iniciales opcionales.
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


# Para qué sirve: Valida actualizaciones parciales de metadatos y estado de un caso existente.
# Cómo funciona: Permite enviar cualquier subconjunto de campos y restringe `status` a `open|closed`.
class CaseUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=140)
    subject_name: Optional[str] = Field(default=None, max_length=140)
    purpose: Optional[str] = Field(default=None, max_length=40)
    notes: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = Field(default=None, pattern="^(open|closed)$")
    friend_scope_enabled: Optional[bool] = None


# Para qué sirve: Valida la adición manual de un identificador semilla dentro de un caso.
# Cómo funciona: Restringe `kind` a tipos soportados y `confidence` a `ALTA|MEDIA|BAJA`.
class IdentifierCreateRequest(BaseModel):
    kind: str = Field(..., pattern="^(username|email|phone|full_name|location|domain|other)$")
    value: str = Field(..., min_length=1, max_length=200)
    source: str = Field(default="manual", max_length=60)
    confidence: str = Field(default="ALTA", pattern="^(ALTA|MEDIA|BAJA)$")


# Para qué sirve: Valida el registro manual de un perfil social encontrado para un caso.
# Cómo funciona: Exige `site` y `url`, acota campos de perfil público y valida nivel `osint_confidence`.
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


# Para qué sirve: Valida el cambio de estado manual de verificación sobre un perfil del caso.
# Cómo funciona: Restringe `manual_status` mediante regex a `auto|confirmed|rejected`.
class ProfileStatusPatchRequest(BaseModel):
    manual_status: str = Field(..., pattern="^(auto|confirmed|rejected)$")


# Para qué sirve: Valida la ingesta directa desde conectores de plataformas públicas abiertas.
# Cómo funciona: Limita `platform` a redes abiertas soportadas, exige `handle` y acota `limit` (1-50).
class ConnectorIngestRequest(BaseModel):
    platform: str = Field(..., pattern="^(bluesky|mastodon|reddit|github|youtube)$")
    handle: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=25, ge=1, le=50)
    include_replies: bool = True
    confirm_legitimate: bool = True


# Para qué sirve: Valida la petición de análisis visual multimodal sobre imágenes del caso.
# Cómo funciona: Selecciona `provider` permitido, si analiza solo pendientes (`only_pending`) y tope `max_images`.
class VisionAnalyzeRequest(BaseModel):
    provider: str = Field(default="auto", pattern="^(auto|local_context|ollama|gemini|openai)$")
    only_pending: bool = False
    max_images: int = Field(default=30, ge=1, le=100)


# Para qué sirve: Valida la selección de proveedor para ejecutar análisis NLP sobre el caso.
# Cómo funciona: Restringe `provider` a `local|auto|ollama|gemini|openai`.
class NlpAnalyzeRequest(BaseModel):
    provider: str = Field(default="local", pattern="^(local|auto|ollama|gemini|openai)$")


# Para qué sirve: Configura las opciones de privacidad y secciones incluidas al exportar el informe del caso.
# Cómo funciona: Controla anonimización de terceros, inclusión de citas/galería y revelación de amigos confirmados.
class CaseReportRequest(BaseModel):
    anonymize_third_parties: bool = True
    include_quotes: bool = True
    include_gallery: bool = True
    reveal_confirmed_friends: bool = False


# Para qué sirve: Valida la petición de limpieza de carpetas temporales en almacenamiento local.
# Cómo funciona: Usa `dry_run=True` por defecto para simular antes de borrar archivos.
class CleanupRequest(BaseModel):
    dry_run: bool = True


# Para qué sirve: Valida el registro manual de una interacción social entre el sujeto y un tercero.
# Cómo funciona: Valida `peer_handle`, restringe `direction` y `kind` y acota contenido/fecha opcionales.
class ManualInteractionRequest(BaseModel):
    peer_handle: str = Field(..., min_length=1, max_length=100)
    direction: str = Field(default="incoming", pattern="^(incoming|outgoing|mutual)$")
    kind: str = Field(default="comment", pattern="^(comment|reply|mention|collab|friend)$")
    platform: str = Field(default="facebook", max_length=40)
    content: Optional[str] = Field(default="", max_length=1000)
    published_at: Optional[str] = Field(default="", max_length=40)


# Para qué sirve: Valida el flujo completo de investigación autorizada de una cuenta en el caso.
# Cómo funciona: Acepta conectores abiertos o OAuth2, `connection_id` y banderas/proveedores de relacional, NLP y SNA.
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


# Para qué sirve: Valida la ejecución independiente de detección de lenguaje relacional del caso.
# Cómo funciona: Restringe `provider` a `local|auto|gemini|ollama|openai`.
class RelationalAnalyzeRequest(BaseModel):
    provider: str = Field(default="local", pattern="^(local|auto|gemini|ollama|openai)$")


# Para qué sirve: Valida la revisión humana (validación, descarte o categoría) de una señal relacional.
# Cómo funciona: Exige `review_status` válido y permite reclasificar `category` y añadir `reviewer_note`.
class RelationalReviewPatchRequest(BaseModel):
    review_status: str = Field(..., pattern="^(pending|validated|ambiguous|dismissed)$")
    category: Optional[str] = Field(default=None, pattern="^(familia|amistad|pareja|laboral_academico|afectivo_general)$")
    reviewer_note: Optional[str] = Field(default=None, max_length=300)


# Para qué sirve: Valida la apertura de sesión interactiva de navegador para login manual.
# Cómo funciona: Restringe `platform` a `instagram` o `facebook`.
class BrowserSessionOpenRequest(BaseModel):
    platform: str = Field(..., pattern="^(instagram|facebook)$")


# Para qué sirve: Valida la limpieza de sesiones/caché del navegador automatizado.
# Cómo funciona: Acepta filtro opcional de `platform` y bandera `dry_run`.
class BrowserCacheCleanRequest(BaseModel):
    platform: Optional[str] = Field(default=None, pattern="^(instagram|facebook)?$")
    dry_run: bool = False


# Para qué sirve: Valida el inicio de una corrida del agente de navegador guiado sobre un perfil autorizado.
# Cómo funciona: Exige plataforma (`instagram|facebook`), `target_url` y límites acotados de posts, comentarios y pausa.
class BrowserAgentStartRequest(BaseModel):
    platform: str = Field(..., pattern="^(instagram|facebook)$")
    target_url: str = Field(..., min_length=2, max_length=400)
    max_posts: int = Field(default=12, ge=1, le=40)
    max_comments_per_post: int = Field(default=15, ge=1, le=40)
    delay_seconds: float = Field(default=2.0, ge=1.0, le=8.0)
    confirm_legitimate: bool = True


# Para qué sirve: Valida la selección o deselección de un ítem en staging antes de persistirlo en el caso.
# Cómo funciona: Exige únicamente el booleano `selected`.
class StagedItemPatchRequest(BaseModel):
    selected: bool


# Para qué sirve: Configura qué motores post-ingesta se disparan al confirmar un lote del agente.
# Cómo funciona: Define banderas booleanas para ejecutar análisis relacional, NLP y grafo SNA.
class AgentCommitRequest(BaseModel):
    run_relational: bool = True
    run_nlp: bool = True
    run_sna: bool = True


# Para qué sirve: Valida el inicio de escaneo de círculo cercano/amigos tras una corrida del agente.
# Cómo funciona: Requiere opt-in `authorized`, referencia opcional `parent_run_id`, plataforma y modo navegador.
class FriendScanStartRequest(BaseModel):
    authorized: bool = False
    parent_run_id: str = ""
    platform: str = "instagram"
    delay_seconds: Optional[float] = None
    headless: bool = False


# Para qué sirve: Configura si el recálculo de pistas geográficas puede usar geocodificación externa.
# Cómo funciona: Expone `use_nominatim` opcional para sobreescribir la configuración por defecto.
class GeoHintsRecomputeRequest(BaseModel):
    use_nominatim: Optional[bool] = None


# Para qué sirve: Valida la investigación geográfica o corrección manual de ubicación sobre una imagen del caso.
# Cómo funciona: Acepta coordenadas/topónimos manuales cuando `manual_override=True` y banderas para VLM y Nominatim.
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


# Para qué sirve: Valida el alta manual de una cuenta amiga aislada mediante su URL pública.
# Cómo funciona: Exige `url` y permite especificar `platform` y etiqueta descriptiva `label`.
class FriendAddRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=500)
    platform: str = "instagram"
    label: str = ""


# Para qué sirve: Valida la ejecución de investigación aislada sobre una cuenta amiga del caso.
# Cómo funciona: Controla si compara visualmente con el sujeto, confirmación `authorized` y uso de `use_vlm`.
class FriendInvestigateRequest(BaseModel):
    compare_with_subject: bool = False
    authorized: bool = True
    use_vlm: bool = False


# Para qué sirve: Valida el enlace explícito de un amigo investigado al grafo SNA del sujeto.
# Cómo funciona: Exige confirmación opt-in `confirm` y transmite puntaje visual y cantidad de coincidencias.
class FriendLinkSnaRequest(BaseModel):
    confirm: bool = False
    visual_proximity_score: float = 0.0
    matches_count: int = 1


# Para qué sirve: Valida la revisión humana sobre una entrada inferida del perfil enriquecido del sujeto.
# Cómo funciona: Restringe `human_review` a `pending|validated|ambiguous|discarded`.
class SubjectProfileReviewRequest(BaseModel):
    human_review: str = Field(..., pattern="^(pending|validated|ambiguous|discarded)$")

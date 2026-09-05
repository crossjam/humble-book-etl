from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, EmailStr


class BundleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    machine_name: str
    tile_name: Optional[str] = None
    tile_short_name: Optional[str] = None
    tile_stamp: Optional[str] = None
    category: Optional[str] = None
    product_url: Optional[str] = None
    start_date_datetime: Optional[datetime] = None
    end_date_datetime: Optional[datetime] = None
    duration_days: Optional[float] = None
    is_active: Optional[bool] = None
    archived_at: Optional[datetime] = None
    price_tiers: Optional[List[Dict[str, Any]]] = None
    book_list: Optional[List[Dict[str, Any]]] = None
    featured_image: Optional[str] = None
    tile_logo: Optional[str] = None
    msrp_total: Optional[float] = None
    verification_date: datetime


class BundleLifecycleEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    bundle_id: Optional[str] = None
    machine_name: str
    bundle_title: Optional[str] = None
    event_type: str
    observed_at: datetime
    previous_start_at: Optional[datetime] = None
    previous_end_at: Optional[datetime] = None
    new_start_at: Optional[datetime] = None
    new_end_at: Optional[datetime] = None
    source_snapshot_id: Optional[str] = None


class BundleHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    record_type: Literal['inactive_bundle', 'lifecycle_event']
    machine_name: str
    bundle_title: Optional[str] = None
    bundle: Optional[BundleResponse] = None
    event: Optional[BundleLifecycleEventResponse] = None
class BundleRawHtmlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    machine_name: str
    raw_html: Optional[str] = None


class ETLRunResponse(BaseModel):
    bundles_processed: int
    cleanup_ran: bool


class LandingPageRawDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    json_data: Dict[str, Any]
    scraped_date: datetime
    source_url: str
    json_hash: Optional[str] = None
    json_version: Optional[str] = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: EmailStr
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128, description='Contraseña en texto plano (el backend la hashea automáticamente)')


class LoginRequest(BaseModel):
    username: str
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    user: UserResponse




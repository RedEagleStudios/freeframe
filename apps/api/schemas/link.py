import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


def _check_url(url: str) -> str:
    """Only absolute http(s) URLs: a link is opened in a new tab by the browser,
    and a `javascript:` or `data:` one would run in FreeFrame's origin."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValueError("Enter a full web address starting with http:// or https://")
    return url


class ProjectLinkCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=2000)
    folder_id: Optional[uuid.UUID] = None  # null = project root

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_url(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v


class ProjectLinkUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    url: Optional[str] = Field(None, min_length=1, max_length=2000)
    folder_id: Optional[uuid.UUID] = None  # use model_fields_set to tell unset from null

    @field_validator("url")
    @classmethod
    def _url(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _check_url(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v


class ProjectLinkResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    folder_id: Optional[uuid.UUID]
    name: str
    url: str
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

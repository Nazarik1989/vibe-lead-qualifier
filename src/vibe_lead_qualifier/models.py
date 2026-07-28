"""Domain and HTTP models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BriefState(BaseModel):
    """Accumulated state of one lead dialog."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str | None = None
    service: str | None = None
    budget: int | None = Field(default=None, ge=0)
    deadline: str | None = None
    contact: str | None = None
    comment: str | None = None
    deal_created: bool = False

    @field_validator("name", "service", "deadline", "contact", "comment")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        return value or None


class BriefPatch(BaseModel):
    """Fields extracted from one message."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = None
    service: str | None = None
    budget: int | None = Field(default=None, ge=0)
    deadline: str | None = None
    contact: str | None = None
    comment: str | None = None


class Attachment(BaseModel):
    """Documented Vibe inbox attachment fields."""

    model_config = ConfigDict(extra="allow")

    type: str
    url: str
    mime: str | None = None
    name: str | None = None


class DemoMessage(BaseModel):
    """Unsigned local input used only by the demonstration endpoint."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dialog_id: str = Field(min_length=1, max_length=255)
    message_id: str | int
    text: str = Field(min_length=1, max_length=20_000)
    channel: str = Field(default="demo", min_length=1, max_length=100)
    context: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)


class BitrixAction(BaseModel):
    """An action accepted by the documented Bitrix24 bridge."""

    method: Literal["crm.deal.add"]
    params: dict[str, Any]


class QualifierReply(BaseModel):
    """Synchronous response to an ``agent.message`` webhook."""

    reply: str
    actions: list[BitrixAction] | None = None


class DialogView(BaseModel):
    dialog_id: str
    brief: BriefState
    missing_fields: list[str]
    ready: bool

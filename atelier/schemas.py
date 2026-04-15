from datetime import datetime

from pydantic import BaseModel, ConfigDict


# --- Tags ---


class TagCreate(BaseModel):
    name: str


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


# --- Images ---


class ImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    prompt_node_id: int
    filename: str
    feedback: str | None
    description: str | None
    kind: str
    discord_message_id: str | None
    discord_channel_id: str | None
    created_at: datetime
    tags: list[TagResponse] = []


class ImageUpdate(BaseModel):
    feedback: str | None = None
    description: str | None = None


# --- Prompt Nodes ---


class PromptNodeCreate(BaseModel):
    parent_id: int | None = None
    name: str | None = None
    prompt_text: str
    notes: str | None = None


class PromptNodeUpdate(BaseModel):
    name: str | None = None
    prompt_text: str | None = None
    notes: str | None = None
    is_starred: bool | None = None


class PromptNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    parent_id: int | None
    name: str | None
    prompt_text: str
    notes: str | None
    is_starred: bool
    created_at: datetime
    image: ImageResponse | None = None
    tags: list[TagResponse] = []


class PromptTreeNode(BaseModel):
    id: int
    parent_id: int | None
    name: str | None
    prompt_text: str
    notes: str | None
    is_starred: bool
    created_at: datetime
    has_image: bool = False
    tags: list[TagResponse] = []
    children: list["PromptTreeNode"] = []


PromptTreeNode.model_rebuild()


# --- Projects ---


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


# --- Coaching ---


class CoachingRequest(BaseModel):
    message: str


class CoachingMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    created_at: datetime

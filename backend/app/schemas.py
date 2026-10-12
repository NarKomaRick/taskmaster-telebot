from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    key: str = Field(pattern=r'^[A-Z][A-Z0-9]{1,11}$')
    description: str = Field(default='', max_length=10000)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Пустое название')
        return value.strip()


class ProjectEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10000)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default='', max_length=50000)
    status_id: int | None = None
    priority: Literal['Low', 'Medium', 'High', 'Critical'] = 'Medium'
    assignee_id: int | None = None
    deadline: date | None = None
    sprint_id: int | None = None

    @field_validator('title')
    @classmethod
    def title_nonblank(cls, value):
        if not value.strip():
            raise ValueError('Пустой заголовок')
        return value.strip()


class TaskEdit(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=50000)
    status_id: int | None = None
    priority: Literal['Low', 'Medium', 'High', 'Critical'] | None = None
    assignee_id: int | None = None
    deadline: date | None = None
    sprint_id: int | None = None
    deleted: bool | None = None


class StatusInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    is_done: bool = False


class StatusOrder(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


class SprintInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date


class SprintEdit(BaseModel):
    state: Literal['active', 'completed']


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class ChecklistEdit(BaseModel):
    done: bool


class InviteInput(BaseModel):
    target: str | None = Field(default=None, max_length=80)


class TimeInput(BaseModel):
    seconds: int = Field(ge=60, le=86400 * 7)
    note: str = Field(default='', max_length=500)


class NotificationInput(BaseModel):
    events: dict[str, bool]


class RepositoryInput(BaseModel):
    full_name: str = Field(pattern=r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', max_length=200)
    token: str | None = Field(default=None, max_length=500)


class GitHubLinkInput(BaseModel):
    url: str = Field(pattern=r'^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:pull/\d+|commit/[a-fA-F0-9]{7,40})$', max_length=1000)


class DeleteProject(BaseModel):
    confirm_key: str


class TransferOwner(BaseModel):
    user_id: int
    confirm_key: str

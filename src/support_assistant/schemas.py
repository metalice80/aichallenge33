from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class User(StrictModel):
    id: str
    name: str
    plan: Literal['free', 'team', 'business']
    auth_method: Literal['sso', 'password']
    product: str
    product_version: str


class TicketMessage(StrictModel):
    author: Literal['user', 'support']
    text: str = Field(max_length=4000)
    created_at: AwareDatetime


class Ticket(StrictModel):
    id: str
    user_id: str
    subject: str = Field(max_length=300)
    status: Literal['open', 'pending', 'closed']
    product: str
    product_version: str
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    messages: list[TicketMessage] = Field(max_length=20)


class ToolResult(StrictModel):
    ok: bool
    data: dict | list | None = None
    code: str | None = None
    message: str | None = None


class DemoSession(StrictModel):
    user_id: str = Field(min_length=1, max_length=64)


class NewConversation(StrictModel):
    ticket_id: str | None = Field(None, max_length=64)


class ChatRequest(StrictModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator('message')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Введите непустое сообщение')
        return value.strip()


class GeneratedAnswer(StrictModel):
    answer: str = Field(min_length=1, max_length=6000)
    source_ids: list[str] = Field(max_length=10)
    needs_clarification: bool


class ActionAnswer(GeneratedAnswer):
    next_steps: list[Annotated[str, Field(min_length=1, max_length=600)]] = Field(min_length=1, max_length=5)

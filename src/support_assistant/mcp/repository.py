"""Only the MCP server reads personal JSON. Replace this Protocol with a CRM adapter."""
import json
from pathlib import Path
from typing import Protocol
from pydantic import TypeAdapter
from support_assistant.schemas import User, Ticket


class Repository(Protocol):
    def users(self) -> list[User]: ...
    def tickets(self) -> list[Ticket]: ...


class JsonRepository:
    def __init__(self, path: Path):
        self._users = TypeAdapter(list[User]).validate_python(json.loads((path / 'users.json').read_text()))
        self._tickets = TypeAdapter(list[Ticket]).validate_python(json.loads((path / 'tickets.json').read_text()))
        ids = {u.id for u in self._users}
        if len(ids) != len(self._users) or len({t.id for t in self._tickets}) != len(self._tickets):
            raise ValueError('Повторяющиеся ID')
        if any(t.user_id not in ids for t in self._tickets):
            raise ValueError('Тикет с неизвестным владельцем')

    def users(self):
        return self._users

    def tickets(self):
        return self._tickets

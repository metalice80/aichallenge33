import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError
from support_assistant.errors import AppError
from support_assistant.schemas import ToolResult, User, Ticket

log = logging.getLogger(__name__)
TOOLS = {'get_current_user', 'list_my_tickets', 'get_ticket', 'list_demo_users'}


class Connection:
    def __init__(self, session, request_id):
        self.session, self.request_id = session, request_id

    async def call(self, name, **args):
        start = time.monotonic()
        result = await self.session.call_tool(name, args)
        log.info('request_id=%s stage=mcp tool=%s seconds=%.3f', self.request_id, name, time.monotonic()-start)
        if result.isError:
            raise AppError('MCP_TOOL_ERROR', 'Ошибка инструмента MCP')
        try:
            parsed = ToolResult.model_validate(result.structuredContent)
        except ValidationError as exc:
            raise AppError('MCP_PROTOCOL', 'Некорректный структурированный результат MCP') from exc
        if not parsed.ok:
            raise AppError(parsed.code or 'MCP_TOOL_ERROR', parsed.message or 'Ошибка MCP',
                           404 if parsed.code == 'NOT_FOUND' else 503)
        if name == 'get_current_user':
            User.model_validate(parsed.data)
        if name == 'get_ticket':
            Ticket.model_validate(parsed.data)
        if name in ('list_demo_users', 'list_my_tickets') and not isinstance(parsed.data, list):
            raise AppError('MCP_PROTOCOL', 'Ожидался список MCP')
        return parsed.data


def find_app_error(exc):
    if isinstance(exc, AppError):
        return exc
    for child in getattr(exc, 'exceptions', []):
        found = find_app_error(child)
        if found:
            return found


class MCPClient:
    def __init__(self, settings):
        self.settings = settings

    @asynccontextmanager
    async def connect(self, user_id=None, request_id='cli'):
        env = dict(os.environ)
        env.pop('MCP_SUBJECT_USER_ID', None)
        env.update(SUPPORT_ROOT=str(self.settings.root), DATA_PATH=str(self.settings.data_path),
                   DEMO_MODE=str(self.settings.demo_mode).lower(), PYTHONUNBUFFERED='1')
        if user_id:
            env['MCP_SUBJECT_USER_ID'] = user_id
        params = StdioServerParameters(command=sys.executable,
                    args=['-m', 'support_assistant.mcp.server'], env=env, cwd=str(self.settings.root))
        try:
            async with asyncio.timeout(self.settings.mcp_timeout_seconds):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        discovered = await session.list_tools()
                        if not TOOLS <= {t.name for t in discovered.tools}:
                            raise AppError('MCP_PROTOCOL', 'MCP не объявил обязательные инструменты')
                        yield Connection(session, request_id)
        except Exception as exc:
            known = find_app_error(exc)
            if known:
                raise known from exc
            raise AppError('MCP_UNAVAILABLE', 'MCP недоступен или превышен тайм-аут; повторите запрос') from exc

    async def catalog(self):
        async with self.connect() as conn:
            return await conn.call('list_demo_users')

    async def context(self, user_id, ticket_id=None, request_id='cli'):
        async with self.connect(user_id, request_id) as conn:
            user = await conn.call('get_current_user')
            if ticket_id:
                ticket = await conn.call('get_ticket', ticket_id=ticket_id)
                return {'user': user, 'ticket': ticket, 'tickets': []}
            tickets = await conn.call('list_my_tickets')
            return {'user': user, 'ticket': None, 'tickets': tickets}

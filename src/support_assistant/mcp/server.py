import os
import logging
from mcp.server.fastmcp import FastMCP
from support_assistant.config import Settings
from support_assistant.mcp.repository import JsonRepository
from support_assistant.schemas import ToolResult

logging.basicConfig(level=logging.WARNING)  # stderr; stdout belongs to MCP
mcp = FastMCP('TeamSpace read-only context')
settings = Settings()
subject = os.environ.get('MCP_SUBJECT_USER_ID')


def run_tool(operation, personal=True):
    try:
        repo = JsonRepository(settings.data_path)
        if personal and not any(u.id == subject for u in repo.users()):
            return ToolResult(ok=False, code='UNAUTHORIZED', message='Требуется демо-сессия')
        return ToolResult(ok=True, data=operation(repo))
    except LookupError:
        return ToolResult(ok=False, code='NOT_FOUND', message='Объект недоступен')
    except Exception:
        return ToolResult(ok=False, code='DATA_INVALID', message='Некорректные JSON-данные; проверьте seed')


@mcp.tool()
def list_demo_users() -> ToolResult:
    """Public synthetic user catalog; available only in demo mode."""
    if not settings.demo_mode:
        return ToolResult(ok=False, code='DEMO_DISABLED', message='Учебный режим отключён')
    return run_tool(lambda r: [{'id': u.id, 'name': u.name} for u in r.users()], personal=False)


@mcp.tool()
def get_current_user() -> ToolResult:
    """Return the immutable process subject, never an ID supplied by the caller."""
    return run_tool(lambda r: next(u.model_dump(mode='json') for u in r.users() if u.id == subject))


@mcp.tool()
def list_my_tickets(status: str | None = None) -> ToolResult:
    """Return summaries belonging to the current subject."""
    if status not in (None, 'open', 'pending', 'closed'):
        return ToolResult(ok=False, code='INVALID_ARGUMENT', message='Неизвестный статус')
    return run_tool(lambda r: [t.model_dump(mode='json', exclude={'messages', 'user_id'})
                              for t in r.tickets() if t.user_id == subject and (not status or t.status == status)])


@mcp.tool()
def get_ticket(ticket_id: str) -> ToolResult:
    """Return a ticket only if it belongs to the process subject."""
    def find(repo):
        for ticket in repo.tickets():
            if ticket.id == ticket_id and ticket.user_id == subject:
                return ticket.model_dump(mode='json')
        raise LookupError()
    return run_tool(find)


if __name__ == '__main__':
    mcp.run(transport='stdio')

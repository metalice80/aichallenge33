import asyncio
import logging
import uuid
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from support_assistant.config import Settings
from support_assistant.errors import AppError
from support_assistant.schemas import DemoSession, NewConversation, ChatRequest
from support_assistant.storage import Storage
from support_assistant.mcp.client import MCPClient
from support_assistant.ollama_client import Ollama
from support_assistant.orchestrator import Orchestrator
from support_assistant.rag.retriever import Retriever
from support_assistant.rag.documents import documents
from support_assistant.rag.indexer import open_index


async def diagnose(settings, mcp, ollama):
    report = {'api':'ok', 'think':settings.ollama_think}
    for name, task in [('ollama', ollama.models), ('mcp', mcp.catalog)]:
        try:
            report[name] = {'status':'ok', 'details': await task()}
        except AppError as exc:
            report[name] = {'status':'error', 'code':exc.code, 'message':exc.message}
    try:
        _, info = await asyncio.to_thread(open_index, settings)
        report['index'] = {'status':'ok', **info}
    except AppError as exc:
        report['index'] = {'status':'error', 'code':exc.code, 'message':exc.message}
    return report


def create_app(settings=None, ollama=None):
    settings = settings or Settings()
    db, mcp = Storage(settings.sqlite_path), MCPClient(settings)
    ollama = ollama or Ollama(settings)
    orchestrator = Orchestrator(settings, db, mcp, Retriever(settings, ollama), ollama)
    app = FastAPI(title='TeamSpace — учебная поддержка', version='0.1.0', docs_url=None, redoc_url=None)
    app.state.orchestrator, app.state.storage = orchestrator, db
    app.state.settings = settings
    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    def error(request, code, message, status):
        return JSONResponse({'code':code,'message':message,'request_id':request.state.request_id}, status_code=status)

    @app.middleware('http')
    async def guard(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        if request.method in ('POST','PUT','DELETE','PATCH'):
            if request.headers.get('origin') not in settings.app_origins.split(','):
                return error(request, 'ORIGIN_REJECTED', 'Недопустимый Origin запроса', 403)
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        logging.getLogger(__name__).warning('request_id=%s code=%s',request.state.request_id,exc.code)
        return error(request, exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return error(request, 'INVALID_REQUEST', 'Проверьте поля запроса; сообщение должно содержать от 1 до 2000 символов', 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, 'NOT_FOUND' if exc.status_code == 404 else 'HTTP_ERROR', 'Запрос недоступен', exc.status_code)

    @app.exception_handler(Exception)
    async def unknown(request, exc):
        logging.getLogger(__name__).error('request_id=%s code=INTERNAL_ERROR type=%s', request.state.request_id, type(exc).__name__)
        return error(request, 'INTERNAL_ERROR', 'Внутренняя ошибка. Проверьте журнал сервера', 500)

    def subject(request):
        return db.user(request.cookies.get('demo_session'))

    @app.get('/')
    async def home():
        return FileResponse(static / 'index.html')

    @app.get('/api/demo/users')
    async def users():
        return await mcp.catalog()

    @app.post('/api/demo/session')
    async def session(body: DemoSession, request: Request, response: Response):
        catalog = await mcp.catalog()
        if body.user_id not in {u['id'] for u in catalog}:
            raise AppError('NOT_FOUND','Пользователь недоступен',404)
        token = db.session(body.user_id,request.cookies.get('demo_session'))
        response.set_cookie('demo_session',token,httponly=True,samesite='strict',secure=False,max_age=86400)
        return {'user_id':body.user_id}

    @app.get('/api/me')
    async def me(request: Request):
        async with mcp.connect(subject(request),request.state.request_id) as conn:
            return await conn.call('get_current_user')

    @app.get('/api/tickets')
    async def tickets(request: Request):
        async with mcp.connect(subject(request),request.state.request_id) as conn:
            return await conn.call('list_my_tickets')

    @app.get('/api/tickets/{ticket_id}')
    async def ticket(ticket_id: str, request: Request):
        async with mcp.connect(subject(request),request.state.request_id) as conn:
            return await conn.call('get_ticket', ticket_id=ticket_id)

    @app.post('/api/conversations')
    async def new(body: NewConversation, request: Request):
        user_id = subject(request)
        if body.ticket_id:
            await mcp.context(user_id, body.ticket_id, request.state.request_id)
        return db.create(user_id, body.ticket_id)

    @app.get('/api/conversations')
    async def conversations(request: Request):
        return db.conversations(subject(request))

    @app.get('/api/conversations/{ident}/messages')
    async def history(ident: str, request: Request):
        return db.history(ident, subject(request))

    @app.post('/api/chat')
    async def chat(body: ChatRequest, request: Request):
        return await orchestrator.answer(subject(request), body.conversation_id, body.message, request.state.request_id)

    @app.get('/api/health')
    async def health():
        return await diagnose(settings, mcp, ollama)

    @app.get('/api/knowledge/{doc_id}', response_class=PlainTextResponse)
    async def knowledge(doc_id: str):
        docs = await asyncio.to_thread(documents, settings.knowledge_base_path)
        if doc_id not in docs:
            raise AppError('NOT_FOUND','Статья не найдена',404)
        return docs[doc_id]['body']

    return app

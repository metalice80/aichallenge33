import json
import httpx
import pytest
from pydantic import ValidationError
from support_assistant.schemas import Ticket, GeneratedAnswer
from support_assistant.mcp.repository import JsonRepository
from support_assistant.ollama_client import Ollama
from support_assistant.errors import AppError
from support_assistant.rag.documents import documents,chunks


@pytest.mark.parametrize('bad', ['duplicate','reference','status','timestamp','json'])
def test_data_validation(settings,bad):
    path=settings.data_path/'tickets.json'
    rows=json.loads(path.read_text())
    if bad=='duplicate':rows.append(rows[0])
    elif bad=='reference':rows[0]['user_id']='unknown'
    elif bad=='status':rows[0]['status']='wrong'
    elif bad=='timestamp':rows[0]['created_at']='2026-10-18T10:00:00'
    path.write_text('{broken' if bad=='json' else json.dumps(rows))
    with pytest.raises((ValueError,ValidationError)):JsonRepository(settings.data_path)


@pytest.mark.parametrize('vectors',[[],[[1],[1]],[[0,0]],[[float('nan')]],[[1],[]]])
async def test_bad_vectors(settings,vectors):
    def handle(request):return httpx.Response(200,content=json.dumps({'embeddings':vectors}))
    client=Ollama(settings,httpx.MockTransport(handle))
    with pytest.raises(AppError) as exc:await client.embed(['test'])
    assert exc.value.code=='EMBEDDING_INVALID'


def test_chunk_bounds_and_metadata(settings):
    docs=documents(settings.knowledge_base_path)
    assert len(docs)>=18
    assert all(len(d['body'])>600 for d in docs.values())
    one=next(iter(docs.values()))
    one['body']='# Заголовок\n\n'+'а'*6000
    result=chunks({'one':one})
    assert max(len(c['text']) for c in result)<=2000
    assert all({'doc_id','chunk_id','section','source_path','content_hash'}<=c['meta'].keys() for c in result)


async def test_mcp_status_and_structure():
    from support_assistant.mcp.client import Connection
    from mcp.types import CallToolResult
    class Session:
        result=CallToolResult(content=[],isError=True)
        async def call_tool(self,*args):return self.result
    session=Session()
    with pytest.raises(AppError) as exc:await Connection(session,'test').call('get_current_user')
    assert exc.value.code=='MCP_TOOL_ERROR'
    session.result=CallToolResult(content=[],structuredContent={'wrong':True})
    with pytest.raises(AppError) as exc:await Connection(session,'test').call('get_current_user')
    assert exc.value.code=='MCP_PROTOCOL'


async def test_personal_identifiers_in_generated_text_rejected(settings):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'message':{'content':json.dumps({
            'answer':'Вы u_002, ваш тикет t_102','source_ids':['auth-sso-cert'],
            'needs_clarification':False})}})
    client=Ollama(settings,httpx.MockTransport(handle))
    with pytest.raises(AppError) as exc:
        await client.generate([{'role':'user','content':'Я u_002'}],['auth-sso-cert','t_101'])
    assert exc.value.code=='LLM_FORMAT' and len(calls)==2

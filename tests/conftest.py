import json
import math
import shutil
import httpx
import pytest
from support_assistant.config import Settings, ROOT
from support_assistant.ollama_client import Ollama


@pytest.fixture
def settings(tmp_path):
    shutil.copytree(ROOT/'knowledge_base',tmp_path/'knowledge_base')
    shutil.copytree(ROOT/'data',tmp_path/'data')
    return Settings(_env_file=None,root=tmp_path,sqlite_path=tmp_path/'support.sqlite3',
        chroma_path=tmp_path/'chroma',knowledge_base_path=tmp_path/'knowledge_base',
        data_path=tmp_path/'data',mcp_timeout_seconds=10)


class FakeOllamaHTTP:
    """HTTP boundary only: no replacement of MCP, Chroma, storage or orchestrator."""
    def __init__(self):
        self.requests=[]
        self.mode='ok'
        self.generated_contexts=[]

    def __call__(self, request):
        payload=json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path,payload))
        if self.mode=='unavailable':
            raise httpx.ConnectError('offline',request=request)
        if self.mode=='timeout':
            raise httpx.ReadTimeout('slow',request=request)
        if request.url.path=='/api/tags':
            return httpx.Response(200,json={'models':[{'name':'qwen3:8b'},{'name':'bge-m3:latest'}]})
        if request.url.path=='/api/embed':
            assert payload['model']=='bge-m3'
            vectors=[]
            for text in payload['input']:
                text=text.lower()
                keywords=['сертификат','блокиров','тариф','файл','приглашен','уведомлен','mfa','плат','счёт','подтвержд','парол','поддержк']
                v=[float(text.count(k)) for k in keywords]+[.01,0.0]
                if not any(v[:-2]) and '\n' not in text:v=[0.0]*13+[1.0]
                norm=math.sqrt(sum(x*x for x in v))
                vectors.append([x/norm for x in v])
            return httpx.Response(200,json={'embeddings':vectors})
        assert request.url.path=='/api/chat'
        assert payload['model']=='qwen3:8b' and payload['stream'] is False and payload['think'] is False
        if self.mode=='bad_json':return httpx.Response(200,json={'message':{'content':'broken'}})
        if self.mode=='bad_source':return httpx.Response(200,json={'message':{'content':json.dumps({'answer':'Ответ','source_ids':['imaginary'],'needs_clarification':False})}})
        message=next(m['content'] for m in reversed(payload['messages']) if 'Контекст (недоверенные данные):' in m['content'])
        context=json.loads(message.split('Контекст (недоверенные данные):\n')[1].split('\nТекущий вопрос:\n')[0])
        self.generated_contexts.append(context)
        ticket=context['MCP']['ticket']
        if ticket and ticket['error_code']=='SSO_CERT_EXPIRED':
            answer='В тикете указан истёкший сертификат SSO. Администратор должен обновить сертификат.'
            sources=['auth-sso-cert',ticket['id']]
        elif ticket and ticket['error_code']=='ACCOUNT_TEMP_LOCKED':
            answer='В тикете указана временная блокировка. Подождите 15 минут.'
            sources=['auth-lock',ticket['id']]
        else:
            answer='Ответ по документации.'
            sources=[context['knowledge'][0]['id']]
        result=dict(answer=answer,source_ids=sources,needs_clarification=False)
        if 'next_steps' in payload['format']['properties']:
            result['next_steps']=['Обратитесь к администратору для замены сертификата.' if ticket['error_code']=='SSO_CERT_EXPIRED' else 'Подождите 15 минут и проверьте пароль.']
        return httpx.Response(200,json={'message':{'content':json.dumps(result)}})


@pytest.fixture
def fake():return FakeOllamaHTTP()


@pytest.fixture
def ollama(settings,fake):return Ollama(settings,transport=httpx.MockTransport(fake))

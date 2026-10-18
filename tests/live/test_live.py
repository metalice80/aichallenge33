import pytest
from support_assistant.config import Settings
from support_assistant.evaluation import evaluate


@pytest.mark.live
async def test_real_models_mcp_and_chroma():
    report=await evaluate(Settings())
    assert report['all_checks_passed'], 'Проверьте фактические ответы в evaluation/live-report.json'


@pytest.mark.live
async def test_general_no_ticket():
    import json
    import httpx
    from support_assistant.api import create_app
    settings=Settings()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)),
            base_url='http://127.0.0.1:8000',headers={'Origin':settings.app_origins.split(',')[0]}) as client:
        (await client.post('/api/demo/session',json={'user_id':'u_004'})).raise_for_status()
        conversation=await client.post('/api/conversations',json={'ticket_id':None})
        conversation.raise_for_status()
        response=await client.post('/api/chat',json={'conversation_id':conversation.json()['id'],
            'message':'Сколько резервных кодов MFA выдаётся и можно ли использовать код повторно?'})
        response.raise_for_status()
        result=response.json()
        (settings.root/'evaluation/general-no-ticket-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        assert '10' in result['answer']
        assert 'в тикете' not in result['answer'].lower()
        assert 'auth-backup' in {s['id'] for s in result['sources']}
        assert all(s['type']!='ticket' for s in result['sources'])

"""Real models + real Chroma + real MCP, through HTTP API. No mocks."""
import json
import time
from datetime import datetime, timezone
import httpx
from support_assistant.api import create_app
from support_assistant.orchestrator import search_query


async def evaluate(settings):
    cases=json.loads((settings.root/'evaluation/cases.json').read_text())
    output=[]
    app=create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000',
                                 headers={'Origin':settings.app_origins.split(',')[0]},timeout=None) as client:
        for case in cases:
            start=time.monotonic()
            row={'id':case['id'],'question':case['question'],'expected_doc_ids':case['expected_doc_ids']}
            try:
                response=await client.post('/api/demo/session',json={'user_id':case['user_id']})
                response.raise_for_status()
                response=await client.post('/api/conversations',json={'ticket_id':case.get('ticket_id')})
                if case.get('expected_error'):
                    row.update(error=response.json(),checks={'access_denied':response.status_code==404 and response.json().get('code')==case['expected_error']})
                else:
                    response.raise_for_status()
                    cid=response.json()['id']
                    context=await app.state.orchestrator.mcp.context(case['user_id'],case.get('ticket_id'))
                    query=search_query(case['question'],[{'role':'user','content':case['previous_question']}] if case.get('previous_question') else [],context)
                    hits=await app.state.orchestrator.retriever.search(query,
                        context['user']['product'],context['user']['product_version'],
                        context['ticket'].get('error_code') if context['ticket'] else None)
                    row['retrieval_probe']=[{'doc_id':h['meta']['doc_id'],'distance':h['distance'],'exact':h['exact']} for h in hits]
                    if case.get('previous_question'):
                        prior=await client.post('/api/chat',json={'conversation_id':cid,'message':case['previous_question']})
                        prior.raise_for_status()
                        row['previous_answer']=prior.json()
                    response=await client.post('/api/chat',json={'conversation_id':cid,'message':case['question']})
                    if response.status_code >= 400 and response.json().get('code') in case.get('allowed_error_codes',[]):
                        row['error']=response.json()
                        row['checks']={'safe_rejection':response.json()['code']=='LLM_FORMAT',
                            'retrieval':set(case['expected_doc_ids']) <= {h['doc_id'] for h in row['retrieval_probe']}}
                        row['generation_status']='safely_rejected'
                        row['seconds']=round(time.monotonic()-start,3)
                        output.append(row)
                        print(f"evaluate {case['id']}: {row['checks']} (safe rejection)",flush=True)
                        continue
                    response.raise_for_status()
                    row['generation_status']='answered'
                    result=response.json()
                    row['result']=result
                    retrieved=result['retrieved_doc_ids']
                    answer=result['answer'].casefold()
                    sources=[]
                    for source in result['sources']:
                        opened=await client.get(source['url'])
                        sources.append({'id':source['id'],'status':opened.status_code,'content':opened.text})
                    row['opened_sources']=sources
                    row['checks']={'retrieval':set(case['expected_doc_ids']) <= set(retrieved),
                        'expected_facts':all(any(term.casefold() in answer for term in (f if isinstance(f,list) else [f])) for f in case['expected_facts']),
                        'forbidden_claims':not any(f.casefold() in answer for f in case['forbidden_claims']),
                        'clarification':result['needs_clarification']==case['needs_clarification'],
                        'sources_open':all(s['status']==200 for s in sources),
                        'expected_citations':set(case.get('expected_source_ids',case['expected_doc_ids'])) <= {s['id'] for s in result['sources']}}
            except Exception as exc:
                row.update(error=str(exc),checks={'request_succeeded':False})
            row['seconds']=round(time.monotonic()-start,3)
            output.append(row)
            print(f"evaluate {case['id']}: {row['checks']}",flush=True)
    relevant=[r for r in output if r['expected_doc_ids']]
    report={'kind':'live','created_at':datetime.now(timezone.utc).isoformat(),
        'models':{'embedding':settings.ollama_embed_model,'chat':settings.ollama_chat_model},
        'top_k':settings.rag_top_k,'max_cosine_distance':settings.rag_max_distance,
        'retrieval_hit_rate':sum(set(r['expected_doc_ids']) <= {h['doc_id'] for h in r.get('retrieval_probe',[])} for r in relevant)/len(relevant),
        'safely_rejected_cases':[r['id'] for r in output if r.get('generation_status')=='safely_rejected'],
        'all_checks_passed':all(all(r['checks'].values()) for r in output),
        'limitations':'Проверки фактов — буквальные подстроки, не семантическая оценка. Открытие источника не доказывает логическую поддержку всех утверждений. Нужен просмотр ответов человеком.',
        'cases':output}
    path=settings.root/'evaluation/live-report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    report['report_path']=str(path)
    return report

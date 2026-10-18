import argparse
import asyncio
import json
import logging
import sys
from support_assistant.config import Settings
from support_assistant.errors import AppError
from support_assistant.mcp.client import MCPClient
from support_assistant.ollama_client import Ollama
from support_assistant.rag.indexer import build_index
from support_assistant.storage import Storage


def output(data):
    print(json.dumps(data,ensure_ascii=False,indent=2))


async def run(command, settings):
    mcp, ollama = MCPClient(settings), Ollama(settings)
    if command == 'seed':
        users = await mcp.catalog()  # validates all JSON inside server, never in backend
        ticket_count = 0
        for user in users:
            context = await mcp.context(user['id'])
            ticket_count += len(context['tickets'])
        Storage(settings.sqlite_path)
        output({'users':len(users),'tickets':ticket_count,'sqlite':str(settings.sqlite_path),'status':'ok'})
    elif command == 'doctor':
        from support_assistant.api import diagnose
        report = await diagnose(settings,mcp,ollama)
        if report['ollama']['status'] == 'ok':
            try:
                probe = await ollama.generate([{'role':'user','content':'Верни answer="Проверка", source_ids=[], needs_clarification=true.'}], [])
                report['generation_probe'] = {'status':'ok','think':settings.ollama_think,'answer':probe.answer}
            except AppError as exc:
                report['generation_probe'] = {'status':'error','code':exc.code,'message':exc.message}
        output(report)
        return 1 if any(isinstance(v,dict) and v.get('status') == 'error' for v in report.values()) else 0
    elif command == 'index':
        output(await build_index(settings,ollama))
    elif command == 'evaluate':
        from support_assistant.evaluation import evaluate
        report = await evaluate(settings)
        output({k:v for k,v in report.items() if k != 'cases'})
        return 0 if report['all_checks_passed'] else 1
    return 0


def main():
    parser=argparse.ArgumentParser(description='Локальный учебный ассистент TeamSpace')
    parser.add_argument('command',choices=['seed','doctor','index','serve','evaluate'])
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    settings=Settings()
    if args.command == 'serve':
        import uvicorn
        from support_assistant.api import create_app
        uvicorn.run(create_app(settings),host=settings.app_host,port=settings.app_port,workers=1)
        return
    try:
        code=asyncio.run(run(args.command,settings))
    except AppError as exc:
        output({'status':'error','code':exc.code,'message':exc.message})
        code=1
    sys.exit(code)


if __name__ == '__main__':
    main()

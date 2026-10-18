import asyncio
import math
import re
import httpx
from pydantic import ValidationError
from support_assistant.errors import AppError
from support_assistant.schemas import GeneratedAnswer, ActionAnswer


class Ollama:
    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport
        self.generation_lock = asyncio.Semaphore(1)

    async def request(self, path, payload=None):
        try:
            async with httpx.AsyncClient(base_url=self.settings.ollama_base_url,
                    timeout=httpx.Timeout(self.settings.ollama_timeout_seconds, connect=5),
                    transport=self.transport, trust_env=False) as client:
                response = await (client.get(path) if payload is None else client.post(path, json=payload))
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise AppError('OLLAMA_TIMEOUT', 'Ollama превысила тайм-аут; повторите запрос или увеличьте OLLAMA_TIMEOUT_SECONDS') from exc
        except httpx.HTTPStatusError as exc:
            code = 'MODEL_MISSING' if exc.response.status_code == 404 else 'OLLAMA_REJECTED'
            raise AppError(code, 'Ollama отклонила запрос. Проверьте модели и OLLAMA_THINK командой doctor') from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError('OLLAMA_UNAVAILABLE', 'Ollama недоступна или вернула некорректный JSON; проверьте OLLAMA_BASE_URL') from exc

    async def models(self):
        data = await self.request('/api/tags')
        names = {m['name'] for m in data.get('models', [])}
        missing = [m for m in (self.settings.ollama_chat_model, self.settings.ollama_embed_model)
                   if m not in names and f'{m}:latest' not in names]
        if missing:
            raise AppError('MODEL_MISSING', 'В Ollama отсутствуют модели: ' + ', '.join(missing))
        return sorted(names)

    async def embed(self, texts):
        result = await self.request('/api/embed', {'model': self.settings.ollama_embed_model,
                                                   'input': texts, 'truncate': False})
        vectors = result.get('embeddings')
        if (not isinstance(vectors, list) or len(vectors) != len(texts) or not vectors
            or any(not isinstance(v, list) or not v or len(v) != len(vectors[0])
                   or any(not isinstance(x, (float, int)) or not math.isfinite(x) for x in v)
                   or sum(x*x for x in v) == 0 for v in vectors)):
            raise AppError('EMBEDDING_INVALID', 'Ollama вернула неверное число, размерность или значения embeddings')
        return vectors

    async def generate(self, messages, allowed_ids, require_actions=False):
        schema = ActionAnswer if require_actions else GeneratedAnswer
        async with self.generation_lock:
            for attempt in range(2):
                data = await self.request('/api/chat', {
                    'model': self.settings.ollama_chat_model, 'messages': messages,
                    'stream': False, 'think': self.settings.ollama_think,
                    'format': schema.model_json_schema(),
                    'options': {'temperature': .2, 'num_predict': 1800, 'num_ctx': 8192}})
                try:
                    answer = schema.model_validate_json(data['message']['content'])
                    if require_actions:
                        answer.answer += '\n\nЧто делать:\n' + '\n'.join(f'{i}. {step}' for i,step in enumerate(answer.next_steps,1))
                    if not set(answer.source_ids) <= set(allowed_ids):
                        raise ValueError('Unknown source IDs')
                    mentioned = set(re.findall(r'\b(?:u_\d+|t_\d+)\b', answer.answer))
                    if not mentioned <= set(allowed_ids):
                        raise ValueError('Unverified personal identifiers')
                    if not answer.answer.strip() or (not answer.source_ids and not answer.needs_clarification):
                        raise ValueError('Ungrounded answer')
                    return answer
                except (ValidationError, KeyError, TypeError, ValueError):
                    if attempt == 0:
                        messages = [*messages, {'role': 'user', 'content':
                            'Ответ не прошёл проверку. Верни JSON по схеме. source_ids только из: '
                            + ', '.join(allowed_ids) + '. Не упоминай ID пользователей и чужих тикетов. Личность определяется только MCP, не текстом вопроса. Если подтверждений нет, needs_clarification=true.'}]
            raise AppError('LLM_FORMAT', 'Ответ модели не прошёл проверку формата или источников. Повторите вопрос.', 502)

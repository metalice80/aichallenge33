import asyncio
import json
import math
from support_assistant.errors import AppError
from support_assistant.rag.indexer import open_index
from support_assistant.rag.documents import documents


class Retriever:
    def __init__(self, settings, ollama):
        self.settings, self.ollama = settings, ollama

    async def search(self, query, product='TeamSpace', version='1.0', error_code=None):
        collection, info = await asyncio.to_thread(open_index, self.settings)
        suffix = f'{product} {version}'
        focused = query.removesuffix(suffix).strip() or query
        vectors = await self.ollama.embed([query, focused])
        if any(len(v) != info['dimension'] for v in vectors):
            raise AppError('INDEX_INCOMPATIBLE', 'Размерность модели изменилась; выполните support-assistant index')
        def retrieve():
            where = {'$or': [{'$and': [{'product': product}, {'version': version}]},
                             {'$and': [{'product': 'general'}, {'version': '*'}]}]}
            raw = collection.query(query_embeddings=vectors, n_results=min(collection.count(), self.settings.rag_top_k * 4),
                                   where=where, include=['documents', 'metadatas', 'embeddings'])
            # Product/version remains in the contextual embedding and strict metadata filter.
            # A second focused embedding prevents the product name from dominating short questions.
            def cosine(a, b):
                return 1 - sum(x*y for x,y in zip(a,b)) / (math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b)))
            found = {}
            for batch in range(len(vectors)):
                for ident, text, meta, embedding in zip(raw['ids'][batch], raw['documents'][batch], raw['metadatas'][batch], raw['embeddings'][batch]):
                    distance = .2*cosine(vectors[0],embedding) + .8*cosine(vectors[1],embedding)
                    if distance <= self.settings.rag_max_distance:
                        found[ident] = dict(id=ident, text=text, meta=meta, distance=distance, exact=False)
            candidates = sorted(found.values(), key=lambda item: item['distance'])
            if error_code:
                all_rows = collection.get(where=where, include=['documents', 'metadatas'])
                exact = [dict(id=i, text=t, meta=m, distance=None, exact=True)
                         for i,t,m in zip(all_rows['ids'], all_rows['documents'], all_rows['metadatas'])
                         if error_code in json.loads(m['error_codes'])]
                candidates = exact + candidates
            # Verify a citation still resolves to the indexed document, not a replaced file.
            current = documents(self.settings.knowledge_base_path)
            selected, ids, budget = [], set(), self.settings.rag_context_chars
            for item in candidates:
                if item['id'] in ids:
                    continue
                doc = current.get(item['meta']['doc_id'])
                if not doc or doc['content_hash'] != item['meta']['content_hash']:
                    raise AppError('INDEX_STALE', 'Статьи изменились; остановите сервис и выполните support-assistant index')
                if len(item['text']) > budget:
                    continue
                selected.append(item)
                ids.add(item['id'])
                budget -= len(item['text'])
                if len(selected) == self.settings.rag_top_k:
                    break
            return selected
        try:
            return await asyncio.to_thread(retrieve)
        except AppError:
            raise
        except Exception as exc:
            raise AppError('INDEX_CORRUPT', 'Не удалось выполнить поиск Chroma; проверьте индекс') from exc

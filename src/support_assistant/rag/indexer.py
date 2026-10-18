import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
import chromadb
from chromadb.config import Settings as ChromaSettings
from support_assistant.errors import AppError
from support_assistant.rag.documents import documents, chunks

SCHEMA = 2
CHUNK_SIZE = 2000
OVERLAP = 200


def client(settings):
    os.environ['ANONYMIZED_TELEMETRY'] = 'False'
    os.environ['OTEL_SDK_DISABLED'] = 'true'
    return chromadb.PersistentClient(path=str(settings.chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False))


def manifest(settings):
    path = settings.chroma_path / 'manifest.json'
    if not path.exists():
        raise AppError('INDEX_MISSING', 'Индекс отсутствует. Выполните support-assistant index')
    try:
        value = json.loads(path.read_text())
        if (value['schema'] != SCHEMA or value['model'] != settings.ollama_embed_model
            or value['chunk_size'] != CHUNK_SIZE or value['overlap'] != OVERLAP
            or value['metric'] != 'cosine' or not isinstance(value['dimension'], int)):
            raise ValueError()
        return value
    except Exception as exc:
        raise AppError('INDEX_INCOMPATIBLE', 'Индекс несовместим. Остановите сервис и выполните support-assistant index') from exc


def open_index(settings):
    info = manifest(settings)
    try:
        collection = client(settings).get_collection(info['collection'], embedding_function=None)
        if not collection.count():
            raise AppError('INDEX_EMPTY', 'Индекс пуст. Выполните support-assistant index')
        return collection, info
    except AppError:
        raise
    except Exception as exc:
        raise AppError('INDEX_CORRUPT', 'Индекс повреждён. Остановите сервис и выполните support-assistant index') from exc


async def build_index(settings, ollama):
    docs = await asyncio.to_thread(documents, settings.knowledge_base_path)
    entries = chunks(docs, CHUNK_SIZE, OVERLAP)
    if not entries:
        raise AppError('KNOWLEDGE_EMPTY', 'В knowledge_base нет содержательных статей')
    vectors = []
    for start in range(0, len(entries), 16):
        vectors.extend(await ollama.embed([f"{e['meta']['title']}\n{e['text']}" for e in entries[start:start+16]]))
    if len({len(v) for v in vectors}) != 1:
        raise AppError('EMBEDDING_INVALID', 'Размерность embeddings меняется между пакетами')

    def publish():
        db = client(settings)
        name = 'knowledge_' + uuid.uuid4().hex
        collection = db.create_collection(name, embedding_function=None,
                                           configuration={'hnsw': {'space': 'cosine'}})
        try:
            collection.add(ids=[e['id'] for e in entries], documents=[e['text'] for e in entries],
                           metadatas=[e['meta'] for e in entries], embeddings=vectors)
            info = dict(schema=SCHEMA, model=settings.ollama_embed_model, dimension=len(vectors[0]),
                        collection=name, metric='cosine', chunk_size=CHUNK_SIZE, overlap=OVERLAP,
                        documents=len(docs), chunks=len(entries), created_at=datetime.now(timezone.utc).isoformat())
            target = settings.chroma_path / 'manifest.json'
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(info, indent=2))
            temp.replace(target)  # old manifest remains usable until complete publication
        except Exception:
            db.delete_collection(name)
            raise
        for old in db.list_collections():
            if old.name.startswith('knowledge_') and old.name != name:
                db.delete_collection(old.name)
        return info
    try:
        return await asyncio.to_thread(publish)
    except AppError:
        raise
    except Exception as exc:
        raise AppError('INDEX_WRITE', 'Не удалось записать индекс Chroma') from exc

import json
import pytest
from support_assistant.errors import AppError
from support_assistant.rag.indexer import build_index,open_index,manifest
from support_assistant.rag.retriever import Retriever


async def test_index_lifecycle_explicit_embeddings(settings,ollama,fake,monkeypatch):
    from chromadb.api.types import DefaultEmbeddingFunction
    def forbidden(self,input):raise AssertionError('Built-in embedding invoked')
    monkeypatch.setattr(DefaultEmbeddingFunction,'__call__',forbidden)
    first=await build_index(settings,ollama)
    again=await build_index(settings,ollama)
    assert first['chunks']==again['chunks']
    collection,_=open_index(settings)
    assert collection._embedding_function is None
    retriever=Retriever(settings,ollama)
    rows=await retriever.search('непонятно','TeamSpace','1.0','SSO_CERT_EXPIRED')
    assert rows[0]['meta']['doc_id']=='auth-sso-cert' and rows[0]['exact']
    assert await retriever.search('квантовый телепорт')==[]
    assert await retriever.search('сертификат','Other','9.0','SSO_CERT_EXPIRED')==[]
    path=settings.knowledge_base_path/'auth-sso-cert.md'
    path.write_text(path.read_text()+'\nИзменённая процедура.\n')
    with pytest.raises(AppError) as exc:await retriever.search('сертификат')
    assert exc.value.code=='INDEX_STALE'
    await build_index(settings,ollama)
    assert any('Изменённая' in r['text'] for r in await retriever.search('сертификат',error_code='SSO_CERT_EXPIRED'))
    path.unlink()
    await build_index(settings,ollama)
    assert all(r['meta']['doc_id']!='auth-sso-cert' for r in await retriever.search('сертификат'))
    assert all(p['model']=='bge-m3' for route,p in fake.requests if route=='/api/embed')


async def test_missing_empty_incompatible_failed_rebuild(settings,ollama,fake):
    with pytest.raises(AppError) as exc:open_index(settings)
    assert exc.value.code=='INDEX_MISSING'
    await build_index(settings,ollama)
    old=manifest(settings)
    fake.mode='unavailable'
    with pytest.raises(AppError):await build_index(settings,ollama)
    assert manifest(settings)==old
    fake.mode='ok'
    file=settings.chroma_path/'manifest.json'
    incompatible=old|{'model':'other'}
    file.write_text(json.dumps(incompatible))
    with pytest.raises(AppError) as exc:open_index(settings)
    assert exc.value.code=='INDEX_INCOMPATIBLE'
    file.write_text(json.dumps(old|{'dimension':123}))
    with pytest.raises(AppError) as exc:await Retriever(settings,ollama).search('сертификат')
    assert exc.value.code=='INDEX_INCOMPATIBLE'
    file.write_text(json.dumps(old))
    collection,_=open_index(settings)
    collection.delete(ids=collection.get()['ids'])
    with pytest.raises(AppError) as exc:open_index(settings)
    assert exc.value.code=='INDEX_EMPTY'

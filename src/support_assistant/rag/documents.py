import hashlib
import json
import re
from pathlib import Path
from pydantic import AwareDatetime, Field
from support_assistant.schemas import StrictModel
from support_assistant.errors import AppError


class Metadata(StrictModel):
    doc_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]*$')
    title: str
    product: str
    version: str
    updated_at: AwareDatetime
    error_codes: list[str] = []


def documents(path: Path):
    result = {}
    try:
        for file in sorted(path.glob('*.md')):
            raw = file.read_text(encoding='utf-8')
            _, header, body = raw.split('---', 2)
            meta = Metadata.model_validate_json(header).model_dump(mode='json')
            if meta['doc_id'] in result:
                raise ValueError('Duplicate doc_id')
            result[meta['doc_id']] = dict(meta=meta, body=body.strip(), source_path=file.name,
                                         content_hash=hashlib.sha256(raw.encode()).hexdigest())
        return result
    except Exception as exc:
        raise AppError('KNOWLEDGE_INVALID', 'Некорректная статья или метаданные базы знаний', 500) from exc


def chunks(docs, size=2000, overlap=200):
    output = []
    for doc in docs.values():
        section, buf, pieces = doc['meta']['title'], '', []
        for paragraph in re.split(r'\n\s*\n', doc['body']):
            if paragraph.startswith('#'):
                if buf:
                    pieces.append((section, buf.strip()))
                    buf = ''
                section = paragraph.splitlines()[0].lstrip('# ').strip()
            if len(buf) + len(paragraph) > size and buf:
                pieces.append((section, buf.strip()))
                buf = ''
            while len(paragraph) > size:
                pieces.append((section, paragraph[:size]))
                paragraph = paragraph[size-overlap:]
            buf += paragraph + '\n\n'
        if buf.strip():
            pieces.append((section, buf.strip()))
        for i, (section, text) in enumerate(pieces):
            # Skip a title-only chunk; titles also appear in each embedding input.
            if len(text.splitlines()) == 1 and text.startswith('#'):
                continue
            meta = {k:v for k,v in doc['meta'].items() if k != 'error_codes'}
            meta.update(chunk_id=f"{meta['doc_id']}:{i}", section=section,
                        error_codes=json.dumps(doc['meta']['error_codes']),
                        source_path=doc['source_path'], content_hash=doc['content_hash'])
            output.append({'id': meta['chunk_id'], 'text': text, 'meta': meta})
    return output

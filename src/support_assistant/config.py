import os
from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(os.environ.get('SUPPORT_ROOT', Path(__file__).resolve().parents[2])).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    root: Path = ROOT
    ollama_base_url: str = 'http://localhost:11434'
    ollama_chat_model: str = 'qwen3:8b'
    ollama_embed_model: str = 'bge-m3'
    ollama_timeout_seconds: float = Field(180, gt=0)
    ollama_think: bool = False
    app_host: str = '127.0.0.1'
    app_port: int = 8000
    app_origins: str = 'http://127.0.0.1:8000,http://localhost:8000'
    demo_mode: bool = True
    sqlite_path: Path = Path('var/support.sqlite3')
    chroma_path: Path = Path('var/chroma')
    knowledge_base_path: Path = Path('knowledge_base')
    data_path: Path = Path('data')
    rag_top_k: int = Field(5, ge=1, le=20)
    rag_max_distance: float = Field(.48, ge=0, le=2)
    rag_context_chars: int = Field(10000, ge=2000, le=30000)
    history_chars: int = Field(6000, ge=1000, le=15000)
    mcp_timeout_seconds: float = Field(20, gt=0)

    @model_validator(mode='after')
    def paths(self):
        for key in ('sqlite_path', 'chroma_path', 'knowledge_base_path', 'data_path'):
            path = getattr(self, key)
            setattr(self, key, (self.root / path).resolve())
        if self.ollama_chat_model != 'qwen3:8b' or self.ollama_embed_model != 'bge-m3':
            raise ValueError('MVP использует только qwen3:8b и bge-m3')
        return self

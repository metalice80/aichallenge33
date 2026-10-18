import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from support_assistant.errors import AppError


def now():
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,ticket_id TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,content TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id));
            CREATE TABLE IF NOT EXISTS attempts(request_id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,
                message TEXT NOT NULL,status TEXT NOT NULL,code TEXT,created_at TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def session(self, user_id, old=None):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            if old:
                db.execute('DELETE FROM sessions WHERE id=?', (old,))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (token, user_id, now()))
        return token

    def user(self, token):
        with self.db() as db:
            row = db.execute('SELECT user_id FROM sessions WHERE id=?', (token,)).fetchone()
        if not row:
            raise AppError('SESSION_REQUIRED', 'Выберите демо-пользователя', 401)
        return row['user_id']

    def create(self, user_id, ticket_id):
        row = dict(id=secrets.token_hex(16), user_id=user_id, ticket_id=ticket_id, created_at=now())
        with self.db() as db:
            db.execute('INSERT INTO conversations VALUES(:id,:user_id,:ticket_id,:created_at)', row)
        return row

    def conversation(self, ident, user_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM conversations WHERE id=? AND user_id=?', (ident, user_id)).fetchone()
        if not row:
            raise AppError('NOT_FOUND', 'Диалог недоступен', 404)
        return dict(row)

    def conversations(self, user_id):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM conversations WHERE user_id=? ORDER BY created_at DESC', (user_id,))]

    def history(self, ident, user_id):
        self.conversation(ident, user_id)
        with self.db() as db:
            return [dict(r) | {'payload': json.loads(r['payload'])} for r in
                    db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id', (ident,))]

    def attempt(self, request_id, conversation, message):
        with self.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)', (request_id,conversation,message,'pending',None,now()))

    def failed(self, request_id, code):
        with self.db() as db:
            db.execute('UPDATE attempts SET status=?,code=? WHERE request_id=?', ('error',code,request_id))

    def complete(self, request_id, conversation, question, result):
        with self.db() as db:
            for role, content, payload in [('user', question, {}), ('assistant', result['answer'], result)]:
                db.execute('INSERT INTO messages(conversation_id,role,content,payload,created_at) VALUES(?,?,?,?,?)',
                           (conversation, role, content, json.dumps(payload,ensure_ascii=False), now()))
            db.execute('UPDATE attempts SET status=? WHERE request_id=?', ('complete', request_id))

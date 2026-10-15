"""SQLite-backed server source of truth. Every business mutation is transactional.

No fake demo rows are seeded; an empty database is an empty system.
"""
from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def ident(prefix: str) -> str:
    return f'{prefix}-{uuid4().hex}'


def jdump(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


class _BufferedCursor:
    """Materialized read cursor.

    Store uses one SQLite connection across FastAPI worker threads. sqlite3 cursors may
    not be fetched after another thread starts using that same connection, even when
    execute itself was locked. Read statements are therefore fully materialized while
    Store.lock is held and exposed through this tiny cursor-compatible wrapper.
    """
    def __init__(self, rows, description=None, *, rowcount=-1, lastrowid=None):
        self._rows=list(rows)
        self._index=0
        self.description=description
        self.rowcount=rowcount
        self.lastrowid=lastrowid

    def fetchone(self):
        if self._index>=len(self._rows):
            return None
        row=self._rows[self._index]
        self._index+=1
        return row

    def fetchmany(self, size=1):
        start=self._index
        end=min(len(self._rows),start+max(0,int(size)))
        self._index=end
        return self._rows[start:end]

    def fetchall(self):
        rows=self._rows[self._index:]
        self._index=len(self._rows)
        return rows

    def __iter__(self):
        while True:
            row=self.fetchone()
            if row is None:
                return
            yield row


class Store:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.infra_bridge = None
        with self.lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA foreign_keys=ON')
            self.conn.execute('PRAGMA busy_timeout=5000')
            self._migrate_legacy_ui_events()
            self.conn.executescript('''
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT NOT NULL, id TEXT NOT NULL, project_id TEXT,
                    body TEXT NOT NULL, PRIMARY KEY(kind,id)
                );
                CREATE INDEX IF NOT EXISTS idx_objects_scope ON objects(kind, project_id);
                CREATE TABLE IF NOT EXISTS artifact_files (
                    version_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL, relative_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL, level TEXT NOT NULL,
                    source TEXT NOT NULL, message TEXT NOT NULL,
                    project_id TEXT, task_id TEXT, trace_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_audit_project ON audit(project_id,seq);
                CREATE TABLE IF NOT EXISTS ui_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    occurred_at TEXT NOT NULL, type TEXT NOT NULL,
                    project_id TEXT, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT NOT NULL,
                    target_id TEXT NOT NULL, agent_id TEXT, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                    error TEXT, result_artifact_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status,created_at);
                CREATE TABLE IF NOT EXISTS context_assembly_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, job_id TEXT,
                    role_id TEXT NOT NULL, profile_id TEXT NOT NULL,
                    query_excerpt TEXT NOT NULL, token_budget INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL, included_json TEXT NOT NULL,
                    excluded_json TEXT NOT NULL, candidates_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_assembly_project
                    ON context_assembly_runs(project_id,created_at);
                CREATE TABLE IF NOT EXISTS backup_runs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    backup_id TEXT, destination TEXT, manifest_path TEXT,
                    file_count INTEGER, checksum_status TEXT, error TEXT,
                    created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_backup_runs_created ON backup_runs(created_at);
                CREATE TABLE IF NOT EXISTS frontier_consult_operations (
                    actor_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, status TEXT NOT NULL,
                    request_id TEXT, error TEXT, created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY(actor_id,operation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_frontier_consult_project
                    ON frontier_consult_operations(project_id,created_at);
                CREATE TABLE IF NOT EXISTS frontier_delegate_operations (
                    actor_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, status TEXT NOT NULL,
                    request_id TEXT, error TEXT, created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY(actor_id,operation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_frontier_delegate_project
                    ON frontier_delegate_operations(project_id,created_at);
                CREATE TABLE IF NOT EXISTS chat_response_groups (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                    user_message_id TEXT NOT NULL, response_mode TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chat_response_groups_channel
                    ON chat_response_groups(channel_id,created_at);
                CREATE TABLE IF NOT EXISTS chat_response_members (
                    group_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    agent_id TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE,
                    route_reason TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(group_id,sequence),
                    FOREIGN KEY(group_id) REFERENCES chat_response_groups(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat_response_members_job
                    ON chat_response_members(job_id);
            ''')
            # Lightweight in-place migrations for Phase 8 runtime/audit fields.
            self._ensure_column('jobs','priority','INTEGER NOT NULL DEFAULT 50')
            self._ensure_column('jobs','attempt','INTEGER NOT NULL DEFAULT 0')
            self._ensure_column('jobs','max_attempts','INTEGER NOT NULL DEFAULT 2')
            self._ensure_column('jobs','cancel_requested','INTEGER NOT NULL DEFAULT 0')
            self._ensure_column('jobs','lease_expires_at','TEXT')
            self._ensure_column('jobs','context_id','TEXT')
            self._ensure_column('jobs','parent_job_id','TEXT')
            self._ensure_column('audit','prev_hash','TEXT')
            self._ensure_column('audit','entry_hash','TEXT')
            self._ensure_column('audit','actor_id','TEXT')


    def _migrate_legacy_ui_events(self):
        """Free the canonical `events` table name for the migrated Infra EventStore.

        Phase 8 used an incompatible UI/SSE event schema under `events`. Existing
        databases are migrated in-place to `ui_events`; new databases create only
        `ui_events`, allowing the authoritative Infra EventStore to own `events`.
        """
        rows=self.conn.execute("PRAGMA table_info(events)").fetchall()
        if not rows:
            return
        columns={row['name'] for row in rows}
        legacy={'seq','id','occurred_at','type','project_id','payload'}
        if not legacy.issubset(columns) or 'event_id' in columns:
            return
        ui_exists=bool(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_events'"
        ).fetchone())
        if not ui_exists:
            self.conn.execute('ALTER TABLE events RENAME TO ui_events')
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO ui_events(seq,id,occurred_at,type,project_id,payload) "
            "SELECT seq,id,occurred_at,type,project_id,payload FROM events"
        )
        self.conn.execute('DROP TABLE events')

    def _ensure_column(self, table:str, column:str, declaration:str):
        cols={row['name'] for row in self.conn.execute(f'PRAGMA table_info({table})').fetchall()}
        if column not in cols:
            self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield self
                self.conn.execute('COMMIT')
            except Exception:
                self.conn.execute('ROLLBACK')
                raise

    def execute(self, sql: str, args=()):
        """Execute with connection-level serialization.

        SQLite's Python connection is shared by FastAPI worker threads. For statements
        returning rows, fetching is part of the critical section; returning a live
        sqlite3.Cursor here previously allowed concurrent requests to interleave reads
        on the same connection and occasionally corrupt observed row values.
        """
        with self.lock:
            cursor=self.conn.execute(sql,args)
            if cursor.description is None:
                return cursor
            rows=cursor.fetchall()
            return _BufferedCursor(
                rows,cursor.description,rowcount=cursor.rowcount,lastrowid=cursor.lastrowid
            )

    def obj(self, kind: str, oid: str) -> dict | None:
        row = self.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, oid)).fetchone()
        return json.loads(row['body']) if row else None

    def all(self, kind: str, project_id: str | None = None) -> list[dict]:
        if project_id is None:
            rows = self.execute('SELECT body FROM objects WHERE kind=? ORDER BY rowid', (kind,)).fetchall()
        else:
            rows = self.execute('SELECT body FROM objects WHERE kind=? AND project_id=? ORDER BY rowid', (kind, project_id)).fetchall()
        return [json.loads(row['body']) for row in rows]

    def put(self, kind: str, body: dict, project_id: str | None = None):
        self.execute('''INSERT INTO objects(kind,id,project_id,body) VALUES(?,?,?,?)
            ON CONFLICT(kind,id) DO UPDATE SET project_id=excluded.project_id,body=excluded.body''',
            (kind, body['id'], project_id, jdump(body)))
        return body

    def delete(self, kind: str, oid: str):
        self.execute('DELETE FROM objects WHERE kind=? AND id=?', (kind, oid))

    def audit(self, source: str, message: str, project_id=None, task_id=None, level='info', trace_id=None, actor_id=None):
        import hashlib
        entry = dict(id=ident('log'), timestamp=now(), level=level,
                     source=source, message=message, projectId=project_id,
                     taskId=task_id, traceId=trace_id, actorId=actor_id)
        prev=self.execute('SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        prev_hash=(prev['entry_hash'] if prev and prev['entry_hash'] else '')
        material=jdump({'id':entry['id'],'timestamp':entry['timestamp'],'level':level,'source':source,'message':message,
                        'projectId':project_id,'taskId':task_id,'traceId':trace_id,'actorId':actor_id,'prevHash':prev_hash})
        entry_hash=hashlib.sha256(material.encode()).hexdigest()
        self.execute('INSERT INTO audit(id,timestamp,level,source,message,project_id,task_id,trace_id,prev_hash,entry_hash,actor_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (entry['id'], entry['timestamp'], level, source, message, project_id, task_id, trace_id, prev_hash, entry_hash, actor_id))
        entry['prevHash']=prev_hash;entry['entryHash']=entry_hash
        return entry

    def emit(self, event_type: str, project_id=None, payload=None):
        event = dict(id=ident('evt'), type=event_type, projectId=project_id,
                     occurredAt=now(), payload=payload or {})
        self.execute('INSERT INTO ui_events(id,occurred_at,type,project_id,payload) VALUES(?,?,?,?,?)',
                     (event['id'], event['occurredAt'], event_type, project_id, jdump(event['payload'])))
        return event

    def change(self, source: str, kind: str, body: dict, project_id=None, task_id=None):
        if self.infra_bridge is not None:
            self.infra_bridge.sync_change(source, kind, body, project_id, task_id)
        self.put(kind, body, project_id)
        self.audit(source, f'{kind} {body["id"]} updated', project_id, task_id)
        self.emit(f'{kind}.changed', project_id, {'id': body['id']})
        return body

    def logs(self, filters: dict):
        clauses = []; args = []
        for key, column in [('projectId', 'project_id'), ('taskId', 'task_id'), ('level','level'), ('source','source'), ('traceId','trace_id')]:
            if filters.get(key): clauses.append(f'{column}=?');args.append(filters[key])
        if filters.get('q'):clauses.append('message LIKE ?');args.append('%'+str(filters['q'])+'%')
        if filters.get('resourceId'):
            clauses.append('(task_id=? OR message LIKE ?)')
            args.extend([str(filters['resourceId']),'%'+str(filters['resourceId'])+'%'])
        if filters.get('since'):clauses.append('timestamp>=?');args.append(filters['since'])
        if filters.get('until'):clauses.append('timestamp<=?');args.append(filters['until'])
        if filters.get('cursor'):
            try: seq = int(filters['cursor'])
            except (ValueError, TypeError): seq = 0
            clauses.append('seq<?');args.append(seq)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        limit = min(max(int(filters.get('limit') or 50), 1), 200)
        rows = self.execute('SELECT * FROM audit'+where+' ORDER BY seq DESC LIMIT ?', (*args,limit+1)).fetchall()
        entries = [{k:r[k] for k in ('id','timestamp','level','source','message')}
                   | {'projectId':r['project_id'], 'taskId':r['task_id'], 'traceId':r['trace_id']} for r in rows[:limit]]
        return {'items':entries,'nextCursor':str(rows[limit-1]['seq']) if len(rows)>limit else None}

    def events_after(self, seq: int, limit=100):
        rows=self.execute('SELECT * FROM ui_events WHERE seq>? ORDER BY seq LIMIT ?', (seq, limit)).fetchall()
        return [(r['seq'],dict(id=r['id'],type=r['type'],projectId=r['project_id'],occurredAt=r['occurred_at'],payload=json.loads(r['payload']))) for r in rows]

    def close(self):
        with self.lock:self.conn.close()

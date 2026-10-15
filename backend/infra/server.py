from __future__ import annotations

import argparse
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .events import EventStore
from .history import HistoryEntry, HistoryStore
from .continuity import ContinuityCheckpoint, ContinuityStore
from .contracts import (
    INFRA_API_VERSION,
    INFRA_CONTRACT_VERSION,
    INFRA_PROFILE_VERSION,
    INFRA_SCHEMA_VERSION,
)


class InfraHandler(BaseHTTPRequestHandler):
    server_version = "MRLC-Infra/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json(
                {
                    "status": "ok",
                    "service": "infra",
                    "api_version": INFRA_API_VERSION,
                    "contract_version": INFRA_CONTRACT_VERSION,
                    "schema_version": INFRA_SCHEMA_VERSION,
                    "profile_version": INFRA_PROFILE_VERSION,
                }
            )
            return
        if parsed.path == "/events":
            project_id = parse_qs(parsed.query).get("project_id", [None])[0]
            if not project_id:
                self._write_json({"error": "project_id is required"}, status=400)
                return
            events = self.server.event_store.read(project_id)  # type: ignore[attr-defined]
            self._write_json(
                {
                    "project_id": project_id,
                    "events": [
                        {
                            "event_id": event.event_id,
                            "event_type": event.event_type,
                            "payload": event.payload,
                            "occurred_at": event.occurred_at.isoformat(),
                        }
                        for event in events
                    ],
                }
            )
            return
        if parsed.path == "/history":
            query = parse_qs(parsed.query)
            project_id = query.get("project_id", [None])[0]
            session_id = query.get("session_id", [None])[0]
            if not project_id or not session_id:
                self._write_json(
                    {"error": "project_id and session_id are required"}, status=400
                )
                return
            entries = self.server.history_store.query(project_id, session_id)  # type: ignore[attr-defined]
            self._write_json(
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "entries": [
                        {
                            "project_id": entry.project_id,
                            "session_id": entry.session_id,
                            "actor": entry.actor,
                            "content": entry.content,
                            "occurred_at": entry.occurred_at.isoformat(),
                        }
                        for entry in entries
                    ],
                }
            )
            return
        if parsed.path == "/continuity/checkpoints/latest":
            query = parse_qs(parsed.query)
            project_id = query.get("project_id", [None])[0]
            git_branch = query.get("git_branch", [None])[0]
            if not project_id:
                self._write_json({"error": "project_id is required"}, status=400)
                return
            checkpoint = self.server.continuity_store.latest(project_id, git_branch)  # type: ignore[attr-defined]
            self._write_json({"checkpoint": None if checkpoint is None else self._checkpoint_json(checkpoint)})
            return
        self._write_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/continuity/checkpoints":
            self._post_checkpoint()
            return
        if parsed.path != "/history":
            self._write_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            entry = HistoryEntry(
                project_id=self._required_string(payload, "project_id"),
                session_id=self._required_string(payload, "session_id"),
                actor=self._required_string(payload, "actor"),
                content=self._required_string(payload, "content"),
                occurred_at=datetime.fromisoformat(
                    self._required_string(payload, "occurred_at")
                ),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._write_json({"error": str(error)}, status=400)
            return
        self.server.history_store.append(entry)  # type: ignore[attr-defined]
        self._write_json({"stored": True}, status=201)

    def _post_checkpoint(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            checkpoint = self.server.continuity_store.create(  # type: ignore[attr-defined]
                project_id=self._required_string(payload, "project_id"),
                session_id=self._required_string(payload, "session_id"),
                project_directory=self._required_string(payload, "project_directory"),
                client=self._required_string(payload, "client"),
                git_branch=self._optional_string(payload, "git_branch"),
                git_head_sha=self._optional_string(payload, "git_head_sha"),
                git_dirty=bool(payload.get("git_dirty", False)),
                objective=self._optional_string(payload, "objective"),
                completed=self._string_tuple(payload, "completed"),
                blockers=self._string_tuple(payload, "blockers"),
                next_steps=self._string_tuple(payload, "next_steps"),
                modified_files=self._string_tuple(payload, "modified_files"),
                verification_status=self._optional_string(payload, "verification_status"),
                created_at=datetime.fromisoformat(self._required_string(payload, "created_at")),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._write_json({"error": str(error)}, status=400)
            return
        self._write_json({"checkpoint": self._checkpoint_json(checkpoint)}, status=201)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _required_string(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} is required")
        return value

    @staticmethod
    def _optional_string(payload: dict[str, object], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string or null")
        return value

    @staticmethod
    def _string_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
        value = payload.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{key} must be a string array")
        return tuple(value)

    @staticmethod
    def _checkpoint_json(checkpoint: ContinuityCheckpoint) -> dict[str, object]:
        return {
            "checkpoint_id": checkpoint.checkpoint_id, "project_id": checkpoint.project_id,
            "session_id": checkpoint.session_id, "project_directory": checkpoint.project_directory,
            "client": checkpoint.client, "git_branch": checkpoint.git_branch,
            "git_head_sha": checkpoint.git_head_sha, "git_dirty": checkpoint.git_dirty,
            "objective": checkpoint.objective, "completed": list(checkpoint.completed),
            "blockers": list(checkpoint.blockers), "next_steps": list(checkpoint.next_steps),
            "modified_files": list(checkpoint.modified_files),
            "verification_status": checkpoint.verification_status,
            "created_at": checkpoint.created_at.isoformat(),
        }


def create_server(
    host: str, port: int, database_path: str | Path
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), InfraHandler)
    server.event_store = EventStore(database_path)  # type: ignore[attr-defined]
    server.history_store = HistoryStore(database_path)  # type: ignore[attr-defined]
    server.continuity_store = ContinuityStore(database_path)  # type: ignore[attr-defined]
    return server


def serve(host: str, port: int, database_path: str | Path) -> None:
    server = create_server(host, port, database_path)
    actual_port = server.server_address[1]
    print(f"infra listening on http://{host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the MRLC Infra bootstrap service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", default=".mrlc/infra.sqlite3")
    args = parser.parse_args()
    Path(args.db).expanduser().parent.mkdir(parents=True, exist_ok=True)
    serve(args.host, args.port, args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

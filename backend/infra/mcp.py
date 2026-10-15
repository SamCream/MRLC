from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool_id: str
    name: str
    allowed_roles: frozenset[str]
    allowed_projects: frozenset[str]
    version: str = "1.0.0"
    credential_ref: str | None = None


@dataclass(frozen=True, slots=True)
class McpAccessEntry:
    tool_id: str
    role_id: str
    project_id: str
    result: str
    recorded_at: datetime


class McpRegistry:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_tools (
                    tool_id TEXT PRIMARY KEY, name TEXT NOT NULL, roles TEXT NOT NULL,
                    projects TEXT NOT NULL, version TEXT NOT NULL DEFAULT '1.0.0', credential_ref TEXT
                );
                CREATE TABLE IF NOT EXISTS mcp_access_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, tool_id TEXT NOT NULL,
                    role_id TEXT NOT NULL, project_id TEXT NOT NULL, result TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(mcp_tools)")}
            if "version" not in columns:
                connection.execute("ALTER TABLE mcp_tools ADD COLUMN version TEXT NOT NULL DEFAULT '1.0.0'")
            if "credential_ref" not in columns:
                connection.execute("ALTER TABLE mcp_tools ADD COLUMN credential_ref TEXT")

    def register(self, tool: ToolDefinition) -> None:
        if tool.credential_ref is not None and not tool.credential_ref.startswith("credential://"):
            raise ValueError("credential reference must use credential://")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO mcp_tools "
                "(tool_id, name, roles, projects, version, credential_ref) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    tool.tool_id, tool.name, ",".join(sorted(tool.allowed_roles)),
                    ",".join(sorted(tool.allowed_projects)), tool.version, tool.credential_ref,
                ),
            )

    def resolve(self, tool_id: str, role_id: str, project_id: str) -> ToolDefinition:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT tool_id, name, roles, projects, version, credential_ref "
                "FROM mcp_tools WHERE tool_id = ?", (tool_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown MCP tool: {tool_id}")
        tool = ToolDefinition(
            row[0], row[1], frozenset(filter(None, row[2].split(","))),
            frozenset(filter(None, row[3].split(","))), row[4], row[5],
        )
        if role_id not in tool.allowed_roles or project_id not in tool.allowed_projects:
            self._record_access(tool_id, role_id, project_id, "DENIED")
            raise PermissionError("MCP tool access denied")
        self._record_access(tool_id, role_id, project_id, "ALLOWED")
        return tool

    def access_log(self, project_id: str) -> list[McpAccessEntry]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT tool_id, role_id, project_id, result, recorded_at "
                "FROM mcp_access_audit WHERE project_id = ? ORDER BY sequence",
                (project_id,),
            ).fetchall()
        return [McpAccessEntry(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4])) for row in rows]

    def _record_access(self, tool_id: str, role_id: str, project_id: str, result: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO mcp_access_audit(tool_id, role_id, project_id, result, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tool_id, role_id, project_id, result, datetime.now(UTC).isoformat()),
            )

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillVersion:
    skill_id: str
    version: str
    capabilities: frozenset[str]
    allowed_roles: frozenset[str] = frozenset()
    allowed_projects: frozenset[str] = frozenset()


class SkillRegistry:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS skills "
                "(skill_id TEXT NOT NULL, version TEXT NOT NULL, capabilities TEXT NOT NULL, "
                "roles TEXT NOT NULL DEFAULT '', projects TEXT NOT NULL DEFAULT '', "
                "PRIMARY KEY (skill_id, version))"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(skills)")}
            if "roles" not in columns:
                connection.execute("ALTER TABLE skills ADD COLUMN roles TEXT NOT NULL DEFAULT ''")
            if "projects" not in columns:
                connection.execute("ALTER TABLE skills ADD COLUMN projects TEXT NOT NULL DEFAULT ''")

    def register(self, skill: SkillVersion) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO skills "
                "(skill_id, version, capabilities, roles, projects) VALUES (?, ?, ?, ?, ?)",
                (
                    skill.skill_id, skill.version, ",".join(sorted(skill.capabilities)),
                    ",".join(sorted(skill.allowed_roles)),
                    ",".join(sorted(skill.allowed_projects)),
                ),
            )

    def resolve(
        self, capability: str, role_id: str | None = None, project_id: str | None = None
    ) -> SkillVersion:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT skill_id, version, capabilities, roles, projects FROM skills"
            ).fetchall()
        candidates = [
            SkillVersion(
                row[0], row[1], frozenset(filter(None, row[2].split(","))),
                frozenset(filter(None, row[3].split(","))),
                frozenset(filter(None, row[4].split(","))),
            )
            for row in rows
            if capability in row[2].split(",")
        ]
        if not candidates:
            raise KeyError(f"no skill provides capability: {capability}")
        authorized = [
            skill for skill in candidates
            if (not skill.allowed_roles or role_id in skill.allowed_roles)
            and (not skill.allowed_projects or project_id in skill.allowed_projects)
        ]
        if not authorized:
            raise PermissionError("skill access denied")
        return max(authorized, key=lambda skill: tuple(int(part) for part in skill.version.split(".")))

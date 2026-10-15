from __future__ import annotations

import json
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_members (
    project_id TEXT NOT NULL, role_id TEXT NOT NULL, module TEXT NOT NULL,
    responsibility TEXT NOT NULL, keywords TEXT NOT NULL, session_id TEXT NOT NULL,
    profile_version TEXT NOT NULL, coordinator INTEGER NOT NULL,
    PRIMARY KEY(project_id,role_id)
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, actor_id TEXT NOT NULL,
    content TEXT NOT NULL, role_id TEXT NOT NULL, session_id TEXT NOT NULL,
    task_id TEXT NOT NULL, state TEXT NOT NULL, reply TEXT, error TEXT,
    reply_to TEXT, created_at REAL NOT NULL, started_at REAL,
    invocation_id TEXT, provider_request_id TEXT, model TEXT, usage_total INTEGER,
    fencing_token INTEGER, route_reason TEXT NOT NULL,
    turn_id TEXT, actor_type TEXT
);
CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, message_id TEXT NOT NULL,
    requester_id TEXT NOT NULL, target_role_id TEXT, phase TEXT NOT NULL,
    turn_complete INTEGER NOT NULL DEFAULT 0, cuttable INTEGER NOT NULL DEFAULT 0,
    routing_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
    completed_at REAL, sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_role_overrides (
    project_id TEXT NOT NULL, role_id TEXT NOT NULL, origin TEXT NOT NULL,
    status TEXT NOT NULL, schema_version TEXT NOT NULL, definition_json TEXT NOT NULL,
    base_profile_version TEXT, created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY(project_id,role_id)
);
CREATE TABLE IF NOT EXISTS conversation_role_grants (
    project_id TEXT NOT NULL, role_id TEXT NOT NULL, authority TEXT NOT NULL,
    granted_by TEXT NOT NULL, granted_at REAL NOT NULL,
    PRIMARY KEY(project_id,role_id,authority)
);
"""

ROLE_ORIGINS = frozenset({"override", "custom"})
ROLE_STATUSES = frozenset({"ACTIVE", "DISABLED"})


class ConversationStore:
    """Infra-owned writer for project conversation state; callers pass their transaction."""

    @staticmethod
    def initialize(connection) -> None:
        connection.executescript(SCHEMA)
        # Additive columns for the group-chat turn model; existing databases upgrade in place.
        ConversationStore._ensure_column(connection, "conversation_messages", "turn_id", "TEXT")
        ConversationStore._ensure_column(connection, "conversation_messages", "actor_type", "TEXT")

    @staticmethod
    def _ensure_column(connection, table, column, declaration) -> None:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def add_member(connection, project_id, role, session_id, profile_version, coordinator) -> None:
        connection.execute("INSERT INTO conversation_members VALUES(?,?,?,?,?,?,?,?)", (
            project_id, role["role_id"], role["module"], role["responsibility"],
            json.dumps(role["keywords"]), session_id, profile_version, int(coordinator)))

    @staticmethod
    def remove_member(connection, project_id, role_id) -> None:
        """Drop one member row; role definitions stay authoritative elsewhere."""
        connection.execute("DELETE FROM conversation_members WHERE project_id=? AND role_id=?",
                           (project_id, role_id))

    @staticmethod
    def members(connection, project_id) -> list:
        return list(connection.execute(
            "SELECT * FROM conversation_members WHERE project_id=? ORDER BY role_id", (project_id,)))

    @staticmethod
    def update_member(connection, project_id, role, coordinator) -> None:
        """Refresh the routing fields of an existing member without changing its session."""
        connection.execute(
            "UPDATE conversation_members SET module=?,responsibility=?,keywords=?,coordinator=? "
            "WHERE project_id=? AND role_id=?",
            (role["module"], role["responsibility"], json.dumps(role["keywords"]), int(coordinator),
             project_id, role["role_id"]))

    @staticmethod
    def add_message(connection, message_id, project_id, actor_id, content, role_id, session_id,
                    task_id, reply_to, route_reason, *, turn_id=None, state="QUEUED",
                    actor_type=None) -> None:
        connection.execute(
            "INSERT INTO conversation_messages(message_id,project_id,actor_id,content,role_id,"
            "session_id,task_id,state,reply_to,created_at,route_reason,turn_id,actor_type) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (message_id, project_id, actor_id, content, role_id, session_id, task_id, state,
             reply_to, time.time(), route_reason, turn_id, actor_type))

    @staticmethod
    def mark_running(connection, message_id, invocation_id, fencing_token) -> None:
        connection.execute(
            "UPDATE conversation_messages SET state='RUNNING',started_at=?,invocation_id=?,"
            "fencing_token=? WHERE message_id=?", (time.time(), invocation_id, fencing_token, message_id))

    @staticmethod
    def mark_unknown(connection, message_id, error) -> None:
        connection.execute("UPDATE conversation_messages SET state='UNKNOWN',error=? WHERE message_id=?",
                           (error, message_id))

    @staticmethod
    def finish(connection, message_id, state, *, reply=None, error=None, provider_request_id=None,
               model=None, usage_total=None) -> None:
        connection.execute(
            "UPDATE conversation_messages SET state=?,reply=?,error=?,provider_request_id=?,"
            "model=?,usage_total=? WHERE message_id=?",
            (state, reply, error, provider_request_id, model, usage_total, message_id))


    # -- project role overrides -------------------------------------------------

    @staticmethod
    def role_overrides(connection, project_id) -> list:
        return list(connection.execute(
            "SELECT * FROM conversation_role_overrides WHERE project_id=? ORDER BY role_id", (project_id,)))

    @staticmethod
    def role_override(connection, project_id, role_id):
        return connection.execute(
            "SELECT * FROM conversation_role_overrides WHERE project_id=? AND role_id=?",
            (project_id, role_id)).fetchone()

    @staticmethod
    def set_role_override(connection, project_id, *, role_id, origin, status, schema_version,
                          definition_json, base_profile_version, created_by, updated_by, now) -> None:
        existing = ConversationStore.role_override(connection, project_id, role_id)
        created_at = existing["created_at"] if existing else now
        connection.execute(
            "INSERT INTO conversation_role_overrides(project_id,role_id,origin,status,schema_version,"
            "definition_json,base_profile_version,created_by,updated_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,role_id) DO UPDATE SET "
            "origin=excluded.origin,status=excluded.status,schema_version=excluded.schema_version,"
            "definition_json=excluded.definition_json,base_profile_version=excluded.base_profile_version,"
            "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (project_id, role_id, origin, status, schema_version, definition_json,
             base_profile_version, created_by, updated_by, created_at, now))

    # -- project role grants ----------------------------------------------------

    @staticmethod
    def role_grants(connection, project_id) -> list:
        return list(connection.execute(
            "SELECT * FROM conversation_role_grants WHERE project_id=? ORDER BY role_id,authority",
            (project_id,)))

    @staticmethod
    def grant_role(connection, project_id, *, role_id, authority, granted_by, now) -> None:
        connection.execute(
            "INSERT INTO conversation_role_grants(project_id,role_id,authority,granted_by,granted_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(project_id,role_id,authority) DO UPDATE SET "
            "granted_by=excluded.granted_by,granted_at=excluded.granted_at",
            (project_id, role_id, authority, granted_by, now))

    # -- conversation turns (group-chat audit units) ----------------------------

    @staticmethod
    def open_turn(connection, *, turn_id, project_id, message_id, requester_id, routing,
                  now) -> None:
        """Record the group-chat turn opened by one user message.

        A turn is the audit unit of the group chat: user message -> routing decision -> role
        work -> any delegation progress. It carries the `cuttable` flag the UI uses to decide
        whether the next role message may be shown before this one finishes (see the ordering
        rule in docs/prd_ui_v1/09).
        """
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM conversation_turns WHERE project_id=?",
            (project_id,)).fetchone()[0]
        connection.execute(
            "INSERT INTO conversation_turns(turn_id,project_id,message_id,requester_id,"
            "target_role_id,phase,turn_complete,cuttable,routing_json,created_at,sequence) "
            "VALUES(?,?,?,?,?,?,0,0,?,?,?)",
            (turn_id, project_id, message_id, requester_id, None, "ROUTING",
             json.dumps(routing or {}, sort_keys=True), now, sequence))

    @staticmethod
    def set_turn_target(connection, turn_id, target_role_id, routing) -> None:
        connection.execute(
            "UPDATE conversation_turns SET target_role_id=?, phase='ASSIGNED', routing_json=? "
            "WHERE turn_id=?", (target_role_id, json.dumps(routing or {}, sort_keys=True), turn_id))

    @staticmethod
    def advance_turn(connection, turn_id, phase, *, complete=False, cuttable=False, now=None) -> None:
        connection.execute(
            "UPDATE conversation_turns SET phase=?, turn_complete=?, cuttable=?, "
            "completed_at=CASE WHEN ?=1 THEN COALESCE(completed_at, ?) ELSE completed_at END "
            "WHERE turn_id=?",
            (phase, int(complete), int(cuttable), int(complete), now, turn_id))

    @staticmethod
    def turn(connection, turn_id):
        return connection.execute("SELECT * FROM conversation_turns WHERE turn_id=?", (turn_id,)).fetchone()

    @staticmethod
    def turns(connection, project_id) -> list:
        return list(connection.execute(
            "SELECT * FROM conversation_turns WHERE project_id=? ORDER BY sequence", (project_id,)))

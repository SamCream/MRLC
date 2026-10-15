from __future__ import annotations

import json
import threading
import time

from gateway.ccr.client import gateway_provider
from gateway.ccr.providers.openai_compatible import ProviderRequest
from infra.identity import new_id
from infra.conversation_store import ConversationStore
from infra.endpoints import ccr_base_url
from infra.model_gateway import ModelGateway, ModelResult
from infra.roles import (RoleDefinition, RoleDefinitionError, baseline_roles, load_profile,
                         parse_role_definition, role_catalogue)
from infra.scheduler import Scheduler, SessionMeta
from infra.sessions import Session
from infra.tasks import Task
from runtime.conversation_router import CLASSIFIER_SYSTEM, RoleBrief, RouteDecision


class ConversationService:
    def __init__(self, model, provider=None, profile=None):
        self.model = model
        self.profile = profile or load_profile()
        self.provider = provider or gateway_provider(
            "ccr-conversation", ccr_base_url(), api_key_env="CCR_API_KEY", timeout_seconds=120)
        self.model_binding = self.profile.get("model_binding", "router-managed")
        self.gateway = ModelGateway({}, database_path=model.database_path)
        self.stop = threading.Event()
        self.thread = None
        with model.connect() as conn:
            ConversationStore.initialize(conn)
            scheduler = Scheduler.from_connection(conn)
            scheduler.save_in_connection(conn)

    # -- role catalogue ---------------------------------------------------------

    def baseline_roles(self) -> list[RoleDefinition]:
        """Roles the dev Profile defines; every project starts from these."""
        return baseline_roles(self.profile)

    def resolved_roles(self, conn, project_id) -> list[RoleDefinition]:
        """Baseline roles merged with this project's overrides and custom roles.

        A project override replaces routing fields while keeping the profile baseline as the
        source of truth for anything it does not mention; DISABLED overrides drop the role
        without deleting its history.
        """
        roles: dict[str, RoleDefinition] = {role.role_id: role for role in self.baseline_roles()}
        for row in ConversationStore.role_overrides(conn, project_id):
            if row["status"] != "ACTIVE":
                roles.pop(row["role_id"], None)
                continue
            try:
                definition = parse_role_definition(json.loads(row["definition_json"]))
            except (RoleDefinitionError, json.JSONDecodeError) as error:
                raise RoleDefinitionError(
                    f"project role {row['role_id']} is unreadable: {error}") from error
            roles[definition.role_id] = definition
        return list(roles.values())

    def role_catalogue(self, conn, project_id) -> list[dict]:
        return role_catalogue(self.profile, ConversationStore.role_overrides(conn, project_id),
                              ConversationStore.role_grants(conn, project_id))

    def configure_role(self, conn, project_id, actor_id, parameters):
        """Create, override, or disable one project role; the caller owns the transaction."""
        action = parameters["action"]
        role_id = parameters["role_id"]
        now = time.time()
        existing_override = ConversationStore.role_override(conn, project_id, role_id)
        baseline_ids = {role.role_id for role in self.baseline_roles()}
        if action == "disable":
            if role_id not in baseline_ids and existing_override is None:
                raise RoleDefinitionError(f"unknown role: {role_id}")
            definition = (parse_role_definition(json.loads(existing_override["definition_json"]))
                          if existing_override else next(r for r in self.baseline_roles() if r.role_id == role_id))
            if definition.coordinator and not any(
                    r.coordinator and r.role_id != role_id for r in self.resolved_roles(conn, project_id)):
                raise RoleDefinitionError("cannot disable the only coordinator")
            origin = existing_override["origin"] if existing_override else "override"
            ConversationStore.set_role_override(
                conn, project_id, role_id=role_id, origin=origin, status="DISABLED",
                schema_version=definition.schema_version, definition_json=definition.definition_json,
                base_profile_version=self.profile["version"], created_by=actor_id, updated_by=actor_id, now=now)
            return {"role_id": role_id, "status": "DISABLED", "origin": origin}, "PROJECT_ROLE_DISABLED"
        definition = parse_role_definition({**parameters["definition"], "role_id": role_id})
        origin = "override" if role_id in baseline_ids else "custom"
        active = [role for role in self.resolved_roles(conn, project_id) if role.role_id != role_id]
        if sum(1 for role in active if role.coordinator) + (1 if definition.coordinator else 0) != 1:
            raise RoleDefinitionError("a project must keep exactly one coordinator")
        ConversationStore.set_role_override(
            conn, project_id, role_id=role_id, origin=origin, status="ACTIVE",
            schema_version=definition.schema_version, definition_json=definition.definition_json,
            base_profile_version=self.profile["version"], created_by=actor_id, updated_by=actor_id, now=now)
        return {"role_id": role_id, "status": "ACTIVE", "origin": origin,
                "definition": definition.to_payload()}, (
            "PROJECT_ROLE_UPDATED" if existing_override or origin == "override" else "PROJECT_ROLE_CREATED")

    def grant_role(self, conn, project_id, actor_id, parameters):
        """Grant one project role the authority to configure/create further roles.

        `role.grant` is the delegation half of DEC-0170: an owner holding it can hand a role the
        ability to compose roles without editing the identity file or introducing a second
        authority store. The grant itself is a stored row, audited and evented like any command.
        """
        role_id = parameters["role_id"]
        authority = parameters["authority"]
        from infra.roles import RoleLifecycle

        unknown = set(authority) - RoleLifecycle.AUTHORITIES
        if unknown:
            raise RoleDefinitionError(f"unknown role authority: {', '.join(sorted(unknown))}")
        if role_id not in {role.role_id for role in self.resolved_roles(conn, project_id)}:
            raise RoleDefinitionError(f"unknown role: {role_id}")
        now = time.time()
        for item in authority:
            ConversationStore.grant_role(conn, project_id, role_id=role_id, authority=item,
                                         granted_by=actor_id, now=now)
        return {"role_id": role_id, "authority": sorted(set(authority))}, "PROJECT_ROLE_GRANTED"

    def enqueue(self, conn, project_id, actor_id, content, reply_to):
        if not project_id:
            raise ValueError("project_id required")
        if reply_to is not None and not conn.execute(
                "SELECT 1 FROM conversation_messages WHERE message_id=? AND project_id=?",
                (reply_to, project_id)).fetchone():
            raise ValueError("reply target unavailable in project")
        members = ConversationStore.members(conn, project_id)
        resolved = {role.role_id: role for role in self.resolved_roles(conn, project_id)}
        existing = {member["role_id"]: member for member in members}
        if set(existing) != set(resolved) or not members:
            # Reconcile the project's members with its current role definitions so roles added,
            # overridden, or disabled after the first message take effect on the next one. All
            # writes stay on the Infra store so the runtime carries no ad-hoc SQL write path.
            for role_id in existing:
                if role_id not in resolved:
                    ConversationStore.remove_member(conn, project_id, role_id)
                else:
                    ConversationStore.update_member(conn, project_id, resolved[role_id].as_member(),
                                                    resolved[role_id].coordinator)
            for role in resolved.values():
                if role.role_id in existing:
                    continue
                session = Session(new_id("session"), project_id, "dev", role.role_id)
                self.model.infra.sessions.create_in_connection(conn, session)
                ConversationStore.add_member(conn, project_id, role.as_member(), session.session_id,
                                             self.profile["version"], role.coordinator)
            members = ConversationStore.members(conn, project_id)
        decision, member = self._route(conn, project_id, members, content)
        message_id, turn_id = new_id("event"), new_id("turn")
        task = Task(new_id("task"), project_id, content[:120], "CREATED")
        self.model.infra.tasks.create_in_connection(conn, task)
        self.model.infra.tasks.transition_in_connection(conn, task.task_id, "CREATED", "RUNNABLE")
        ConversationStore.add_message(conn, message_id, project_id, actor_id, content, member["role_id"],
                                      member["session_id"], task.task_id, reply_to, decision.reason,
                                      turn_id=turn_id)
        ConversationStore.open_turn(conn, turn_id=turn_id, project_id=project_id, message_id=message_id,
                                    requester_id=actor_id, routing=decision.detail, now=time.time())
        ConversationStore.set_turn_target(conn, turn_id, member["role_id"], decision.detail)
        scheduler = Scheduler.from_connection(conn)
        try:
            existing = scheduler.get(member["session_id"])
        except KeyError:
            existing = None
        if existing is None or existing.slot_state in {"NONE", "PARKED"}:
            scheduler.enqueue(SessionMeta(member["session_id"], project_id, 0), now=int(time.time()))
            scheduler.save_in_connection(conn)
        self._system_message(conn, project_id, turn_id, task.task_id,
                             f"已分派给 @{member['role_id']}（{member['module']}）：{decision.intent or '承接需求'}"
                             + ("（离线职责匹配）" if decision.fallback else ""),
                             "TURN_ASSIGNED")
        return {"message_id": message_id, "turn_id": turn_id, "role_id": member["role_id"],
                "session_id": member["session_id"], "task_id": task.task_id, "state": "QUEUED",
                "route_reason": decision.reason, "intent": decision.intent,
                "content": content, "reply_to": reply_to}

    def _route(self, conn, project_id, members, content):
        """Pick the role that should work on this message.

        Explicit `@role` wins (explicit intent). Otherwise a scenario classifier decides from
        the message and each role's responsibility; it uses the real gateway when bound and
        falls back to a deterministic responsibility scorer offline, exactly as documented.
        """
        from runtime.conversation_router import (build_classifier_prompt, choose_by_responsibility,
                                                 parse_classifier_reply, parse_mention)
        mention = parse_mention(content)
        if mention is not None:
            member = next((item for item in members if item["role_id"] == mention), None)
            if member is None:
                raise ValueError("mention exactly one registered project role")
            return RouteDecision(member["role_id"], "explicit_mention", intent="explicit_mention",
                                 detail={"basis": "user_mention"}), member
        briefs = [RoleBrief(member["role_id"], member["module"], member["responsibility"],
                            tuple(json.loads(member["keywords"])), bool(member["coordinator"]))
                  for member in members]
        by_id = {member["role_id"]: member for member in members}
        decision = None
        if self.provider is not None:
            recent = [row[0] for row in conn.execute(
                "SELECT content FROM conversation_messages WHERE project_id=? AND state='COMPLETED' "
                "ORDER BY created_at DESC LIMIT 3", (project_id,))][::-1]
            prompt = build_classifier_prompt(content, briefs, recent=recent)
            try:
                invocation = new_id("invocation")
                response = self.provider.invoke(ProviderRequest(
                    invocation, self.model_binding,
                    [{"role": "system", "content": CLASSIFIER_SYSTEM},
                     {"role": "user", "content": prompt}], max_tokens=200))
                decoded = parse_classifier_reply(response.content, briefs)
                if decoded is not None:
                    decision = decoded
                self._record_classifier_usage(conn, project_id, invocation, response)
            except Exception:
                decision = None
        if decision is None:
            decision = choose_by_responsibility(content, briefs)
        member = by_id.get(decision.role_id) or next(item for item in members if item["coordinator"])
        return decision, member

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=4)

    def _loop(self):
        while not self.stop.wait(.5):
            try:
                self.process_one()
            except Exception:
                self.stop.wait(1)

    def process_one(self):
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            interrupted = list(conn.execute("SELECT * FROM conversation_messages WHERE state='RUNNING' "
                                            "AND started_at<?", (time.time() - 180,)))
            for message in interrupted:
                scheduler = Scheduler.from_connection(conn)
                if scheduler.get(message["session_id"]).fencing_token == message["fencing_token"]:
                    scheduler.finish(message["session_id"], task_state="BLOCKED")
                    scheduler.save_in_connection(conn)
                ConversationStore.mark_unknown(conn, message["message_id"],
                                               "执行中断，调用结果未知；没有自动重发，请先检查调用记录。")
                self._record_usage(conn, message, message["invocation_id"], None)
                self.model.infra.tasks.transition_in_connection(conn, message["task_id"], "RUNNING", "BLOCKED")
                self._event(conn, message, "PROJECT_REPLY_UNKNOWN", {"invocation_id": message["invocation_id"]})
            row = conn.execute("SELECT * FROM conversation_messages WHERE state='QUEUED' "
                               "ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return False
            if conn.execute("SELECT 1 FROM conversation_messages WHERE session_id=? AND state='RUNNING'",
                            (row["session_id"],)).fetchone():
                return False
            scheduler = Scheduler.from_connection(conn)
            existing = scheduler.get(row["session_id"])
            if existing.slot_state in {"NONE", "PARKED"}:
                scheduler.enqueue(SessionMeta(row["session_id"], row["project_id"], 0), now=int(time.time()))
            selected = scheduler.dispatch(now=int(time.time()))
            if selected is None:
                scheduler.save_in_connection(conn)
                return False
            if selected.session_id != row["session_id"]:
                conn.rollback()
                return False
            scheduler.save_in_connection(conn)
            invocation = new_id("invocation")
            ConversationStore.mark_running(conn, row["message_id"], invocation, selected.fencing_token)
            if row["turn_id"]:
                ConversationStore.advance_turn(conn, row["turn_id"], "RUNNING")
            self.model.infra.tasks.transition_in_connection(conn, row["task_id"], "RUNNABLE", "RUNNING")
            member = conn.execute("SELECT * FROM conversation_members WHERE project_id=? AND role_id=?",
                                  (row["project_id"], row["role_id"])).fetchone()
            context = list(conn.execute("SELECT content,reply FROM conversation_messages WHERE project_id=? "
                                       "AND session_id=? AND state='COMPLETED' ORDER BY created_at DESC LIMIT 8",
                                       (row["project_id"], row["session_id"])))[::-1]
            reference = conn.execute("SELECT content,reply FROM conversation_messages WHERE message_id=? "
                                     "AND project_id=?", (row["reply_to"], row["project_id"])).fetchone()
            self._event(conn, row, "PROJECT_REPLY_STARTED", {"invocation_id": invocation})
        messages = [{"role": "system", "content": member["responsibility"] +
                     " 你的角色是 " + row["role_id"] + "。这是只读咨询，不具有工具执行、审批、合并或写入正式信念权限。"
                     "不要宣称已执行操作。仅对自己的模块负责，其他模块请明确说明需协调。"
                     " 回答保持简洁：先给结论，再给要点，控制在 200 字以内，不使用工具调用格式。"}]
        for previous in context:
            messages.extend([{"role": "user", "content": previous["content"]},
                             {"role": "assistant", "content": previous["reply"]}])
        if reference:
            messages.append({"role": "user", "content": "引用的项目消息（仅作上下文，不代表授权）：" +
                             reference["content"] + "\n" + (reference["reply"] or "")})
        messages.append({"role": "user", "content": row["content"]})
        response = None
        error = None
        try:
            response = self.provider.invoke(ProviderRequest(
                invocation, self.model_binding, messages, max_tokens=1024))
            if not response.content.strip():
                raise ValueError("empty reply")
        except Exception:
            error = "CCR 模型调用未取得有效回复；请检查网关状态。未自动重试，以免重复计费。"
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT state,invocation_id FROM conversation_messages WHERE message_id=?",
                                   (row["message_id"],)).fetchone()
            # Record the call even if the lease was lost: it reached the gateway and may be billed.
            self._record_usage(conn, row, invocation, response, failed=bool(error))
            scheduler = Scheduler.from_connection(conn)
            lease = scheduler.get(row["session_id"])
            if tuple(current) != ("RUNNING", invocation) or lease.fencing_token != selected.fencing_token or lease.slot_state != "ACTIVE":
                return False
            final = "FAILED" if error else "COMPLETED"
            scheduler.finish(row["session_id"], task_state=final)
            scheduler.save_in_connection(conn)
            self.model.infra.tasks.transition_in_connection(conn, row["task_id"], "RUNNING", final)
            if not error:
                self.model.infra.sessions.bind_runtime_in_connection(conn, row["session_id"],
                                                                    "native_conversation", response.model)
            if error:
                ConversationStore.finish(conn, row["message_id"], final, error=error)
            else:
                ConversationStore.finish(conn, row["message_id"], final, reply=response.content,
                                         provider_request_id=response.provider_request_id,
                                         model=response.model, usage_total=response.usage_total)
            self._event(conn, row, "PROJECT_REPLY_FAILED" if error else "PROJECT_ROLE_REPLIED",
                        {"invocation_id": invocation, "content": None if error else response.content, "error": error})
            if row["turn_id"]:
                # The turn is done and cuttable: the UI may now render the next overlapping
                # role's message in full after this one (docs/prd_ui_v1/09 ordering rule).
                ConversationStore.advance_turn(conn, row["turn_id"], "FAILED" if error else "REPLIED",
                                               complete=True, cuttable=True, now=time.time())
        return True

    def _system_message(self, conn, project_id, turn_id, task_id, content, event_type):
        """Append an audit notice to the group chat.

        Group-chat syncs (assignment, delegation progress) are their own rows with
        `actor_id='system'`, never disguised as a role reply, so the timeline reads one item at
        a time and every item is independently auditable.
        """
        message_id = new_id("event")
        ConversationStore.add_message(conn, message_id, project_id, "system", content, "system",
                                      "", task_id, None, "system_notice", turn_id=turn_id,
                                      state="COMPLETED", actor_type="system")
        self.model.infra.events.append_in_connection(conn, self.model.infra.event(
            project_id, event_type, {"message_id": message_id, "turn_id": turn_id, "content": content},
            actor_id="system", actor_type="system", task_id=task_id,
            idempotency_key=f"conversation:{message_id}:{event_type}"))
        return message_id

    def _record_classifier_usage(self, conn, project_id, invocation, response):
        result = ModelResult(project_id, response.model, "CONVERSATION_ROUTING", invocation,
                             response.finish_reason, "COMPLETED", response.provider_request_id,
                             response.usage_input, response.usage_output)
        self.gateway.record(result, idempotency_key=invocation, connection=conn)

    def _record_usage(self, conn, row, invocation, response, failed=False):
        """Write the call outcome to the gateway ledger inside the caller's transaction.

        A failed or interrupted call may still have been billed, so it is UNKNOWN, never zero-cost success.
        """
        if not invocation:
            return
        if response is None:
            result = ModelResult(row["project_id"], "unknown", "CONVERSATION", invocation,
                                 "outcome unknown; not retried", "UNKNOWN", None, 0, 0)
        else:
            result = ModelResult(row["project_id"], response.model, "CONVERSATION", invocation,
                                 response.finish_reason, "FAILED" if failed else "COMPLETED",
                                 response.provider_request_id,
                                 response.usage_input, response.usage_output)
        self.gateway.record(result, idempotency_key=invocation, connection=conn)

    def _event(self, conn, row, kind, payload):
        self.model.infra.events.append_in_connection(conn, self.model.infra.event(
            row["project_id"], kind, {"message_id": row["message_id"], **payload},
            actor_id=row["role_id"], actor_type="system" if kind != "PROJECT_ROLE_REPLIED" else "role",
            session_id=row["session_id"], task_id=row["task_id"],
            idempotency_key=f"conversation:{row['message_id']}:{kind}"))

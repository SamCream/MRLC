"""Scenario-based role routing for the project group chat.

The group chat must decide *which agent should work on this* from the situation, not from
keyword triggers (see docs/prd_ui_v1/09 UI-026 and the 2026-10-11 design note). A single
classifier call goes through the gateway like any other model call, so the decision is real,
metered, and auditable; when no gateway is bound (tests, offline) a deterministic
responsibility-overlap scorer keeps the same contract so behaviour stays testable.

Roles' `keywords` survive as *hints* in the classifier prompt only; they never select a role
by themselves.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RoleBrief:
    role_id: str
    module: str
    responsibility: str
    hints: tuple[str, ...] = ()
    coordinator: bool = False


@dataclass(frozen=True, slots=True)
class RouteDecision:
    role_id: str
    reason: str
    intent: str = ""
    fallback: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


STOPWORDS = frozenset({"the", "and", "for", "with", "how", "what", "this", "that", "please",
                       "are", "you", "can", "should", "would", "does", "about"})


def _tokens(text: str) -> set[str]:
    latin = {token for token in re.findall(r"[a-z0-9]+", text.casefold()) if token not in STOPWORDS}
    cjk = {text[index:index + 2] for index in range(len(text) - 1)
           if "\u4e00" <= text[index] <= "\u9fff" and "\u4e00" <= text[index + 1] <= "\u9fff"}
    cjk |= {char for char in text if "\u4e00" <= char <= "\u9fff"}
    return latin | cjk


def score_overlap(content: str, brief: RoleBrief) -> int:
    """Deterministic responsibility overlap used as the offline classifier.

    Counts how many of a role's hints and responsibility words appear in the request. Kept
    deliberately simple and explainable; the real path is the model classifier.
    """
    haystack = content.casefold()
    hits = 0
    for hint in brief.hints:
        if hint and hint.casefold() in haystack:
            hits += 2
    wanted = _tokens(brief.module + " " + brief.responsibility)
    hits += len(wanted & _tokens(content))
    return hits


def choose_by_responsibility(content: str, briefs: list[RoleBrief]) -> RouteDecision:
    """Offline/fallback classifier: highest responsibility overlap, coordinator on ties."""
    scored = sorted(
        ((score_overlap(content, brief), brief) for brief in briefs),
        key=lambda item: (-item[0], not item[1].coordinator, item[1].role_id),
    )
    top_score, top = scored[0]
    if top_score == 0:
        coordinator = next((brief for brief in briefs if brief.coordinator), None)
        if coordinator is None:
            raise ValueError("profile requires one coordinator")
        return RouteDecision(coordinator.role_id, "scenario_classifier",
                             intent="ambiguous", fallback=True,
                             detail={"score": 0, "basis": "no_responsibility_overlap"})
    return RouteDecision(top.role_id, "scenario_classifier", intent="responsibility_match",
                         fallback=True, detail={"score": top_score, "basis": "responsibility_overlap"})


def parse_mention(content: str) -> str | None:
    mentions = re.findall(r"(?<!\w)@([\w.-]+)", content)
    unique = set(mentions)
    if not mentions:
        return None
    if len(unique) != 1:
        raise ValueError("mention exactly one registered project role")
    return next(iter(unique))


CLASSIFIER_SYSTEM = (
    "你是 MRLC 项目群聊的分派器。根据用户这条消息的使用场景，从候选角色中选出最合适执行工作的"
    "一个角色。只输出一行 JSON：{\"role_id\": \"...\", \"intent\": \"...\", \"reason\": \"...\"}。"
    "role_id 必须来自候选列表；intent 用短语概括任务类型；reason 说明为什么该角色最合适。"
    "不要输出任何其他文本，不要调用工具。"
)


def build_classifier_prompt(content: str, briefs: list[RoleBrief], *, recent: list[str]) -> str:
    lines = ["候选角色："]
    for brief in briefs:
        hints = "、".join(brief.hints)
        coordinator = "（协调者）" if brief.coordinator else ""
        lines.append(f"- {brief.role_id}{coordinator}｜模块：{brief.module}｜职责：{brief.responsibility}"
                     + (f"｜线索：{hints}" if hints else ""))
    if recent:
        lines.append("最近上下文：" + " / ".join(recent[-3:]))
    lines.append("用户消息：" + content)
    lines.append('请只输出 JSON：{"role_id": "...", "intent": "...", "reason": "..."}')
    return "\n".join(lines)


def parse_classifier_reply(reply: str, briefs: list[RoleBrief]) -> RouteDecision | None:
    """Parse a classifier reply; return None when unusable so the caller can fall back."""
    text = (reply or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    role_id = payload.get("role_id")
    if not isinstance(role_id, str) or role_id not in {brief.role_id for brief in briefs}:
        return None
    return RouteDecision(role_id, "scenario_classifier",
                         intent=str(payload.get("intent") or "").strip()[:120],
                         fallback=False,
                         detail={"reason": str(payload.get("reason") or "").strip()[:400]})

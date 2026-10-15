"""Backend-only observability endpoints for the migrated Infra core.

No current React page depends on these routes; they are intentionally additive.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from fastapi import Depends
from .common import logged_in, page, project, bad, nonempty, list_of_str, oneof
from .storage import ident
from infra.memory import MemoryItem, EvidenceItem
from infra.project_bank import Issue, Decision, Lesson, Progress


def register(api,store,runtime):
    core=api.state.infra

    @api.get('/api/v1/infra/status')
    def infra_status(_:Any=Depends(logged_in)):
        return core.status()

    @api.get('/api/v1/projects/{project_id}/infra/events')
    def infra_events(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return page([{
            'sequence':e.sequence,'eventId':e.event_id,'type':e.event_type,
            'occurredAt':e.occurred_at.isoformat(),'actorId':e.actor_id,
            'sessionId':e.session_id,'taskId':e.task_id,'correlationId':e.correlation_id,
            'causationId':e.causation_id,'payload':e.payload,
        } for e in core.events.read(project_id)])

    @api.get('/api/v1/projects/{project_id}/infra/replay')
    def infra_replay(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        state=core.replay(project_id)
        return asdict(state)

    @api.get('/api/v1/projects/{project_id}/infra/memory')
    def infra_memory(project_id:str,roleId:str='shared',_:Any=Depends(logged_in)):
        project(store,project_id)
        return page([asdict(x) for x in core.memory.active(project_id,roleId,datetime.now(timezone.utc))])

    @api.get('/api/v1/projects/{project_id}/infra/bank/issues')
    def infra_issues(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return page([asdict(x) for x in core.bank.open_issues(project_id)])


    @api.post('/api/v1/projects/{project_id}/infra/memory',status_code=201)
    def create_memory(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        refs=tuple(list_of_str(body.get('sourceEventRefs',[]),'sourceEventRefs'))
        if not refs:bad('sourceEventRefs must contain at least one domain event')
        confidence=float(body.get('confidence',0.5))
        if not 0.0<=confidence<=1.0:bad('confidence must be between 0 and 1')
        evidence_body=body.get('evidence')
        if not isinstance(evidence_body,list) or not evidence_body:bad('evidence must contain at least one item')
        evidence=[]
        for raw in evidence_body:
            if not isinstance(raw,dict):bad('evidence items must be objects')
            event_id=nonempty(raw.get('eventId'),'evidence.eventId',160)
            evidence_confidence=float(raw.get('confidence',confidence))
            if not 0.0<=evidence_confidence<=1.0:bad('evidence confidence must be between 0 and 1')
            evidence.append(EvidenceItem(
                ident('evidence'),event_id,
                oneof(raw.get('stance','supports'),('supports','contradicts','neutral'),'evidence.stance'),
                nonempty(raw.get('sourceRef'),'evidence.sourceRef',1000),
                evidence_confidence,
            ))
        try:
            valid_from=datetime.fromisoformat(str(body.get('validFrom') or datetime.now(timezone.utc).isoformat()).replace('Z','+00:00'))
            valid_to=(datetime.fromisoformat(str(body['validTo']).replace('Z','+00:00')) if body.get('validTo') else None)
        except ValueError:
            bad('validFrom/validTo must be ISO-8601 timestamps')
        if valid_to is not None and valid_to<=valid_from:bad('validTo must be later than validFrom')
        item=MemoryItem(
            ident('memory'),project_id,str(body.get('roleScope') or 'shared')[:120],
            nonempty(body.get('kind'),'kind',120),nonempty(body.get('content'),'content',100000),
            refs,valid_from,confidence,'active',valid_to,
        )
        try:
            with store.tx():
                core.record_memory(item,tuple(evidence),actor_id=principal.id)
        except (KeyError,ValueError,PermissionError) as exc:
            bad(str(exc),400)
        return {**asdict(item),'evidence':[asdict(x) for x in evidence]}

    @api.post('/api/v1/projects/{project_id}/infra/memory/{memory_id}/supersede')
    def supersede_memory(project_id:str,memory_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        replacement=nonempty(body.get('replacementMemoryId'),'replacementMemoryId',160)
        try:
            with store.tx():
                core.supersede_memory(project_id,memory_id,replacement,actor_id=principal.id)
        except (KeyError,ValueError,PermissionError) as exc:
            bad(str(exc),400)
        return {'memoryId':memory_id,'status':'superseded','replacementMemoryId':replacement}

    @api.post('/api/v1/projects/{project_id}/infra/bank/issues',status_code=201)
    def create_bank_issue(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        issue=Issue(
            ident('issue'),project_id,nonempty(body.get('title'),'title',500),
            str(body.get('blocking') or 'soft')[:120],
            str(body.get('decisionOwner') or principal.id)[:160],
            bool(body.get('autoDecidable',False)),bool(body.get('reversible',False)),
        )
        with store.tx():core.create_issue(issue,actor_id=principal.id)
        return asdict(issue)

    @api.post('/api/v1/projects/{project_id}/infra/bank/decisions',status_code=201)
    def create_bank_decision(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        supersedes=body.get('supersedes')
        if supersedes is not None: supersedes=nonempty(supersedes,'supersedes',160)
        decision=Decision(
            ident('decision'),project_id,nonempty(body.get('title'),'title',500),
            str(body.get('authority') or principal.id)[:160],
            str(body.get('risk') or 'unknown')[:120],
            nonempty(body.get('rationale'),'rationale',10000),supersedes=supersedes,
        )
        try:
            with store.tx():core.record_decision(decision,actor_id=principal.id)
        except (KeyError,ValueError,PermissionError) as exc:
            bad(str(exc),400)
        return asdict(decision)

    @api.post('/api/v1/projects/{project_id}/infra/bank/issues/{issue_id}/resolve')
    def resolve_bank_issue(project_id:str,issue_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        decision_id=nonempty(body.get('decisionId'),'decisionId',160)
        try:
            with store.tx():
                resolved=core.resolve_issue(project_id,issue_id,decision_id,actor_id=principal.id)
        except (KeyError,ValueError,PermissionError) as exc:
            bad(str(exc),400)
        return asdict(resolved)

    @api.post('/api/v1/projects/{project_id}/infra/bank/lessons',status_code=201)
    def create_bank_lesson(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        refs=tuple(list_of_str(body.get('sourceRefs',[]),'sourceRefs'))
        if not refs:bad('sourceRefs must not be empty')
        lesson=Lesson(ident('lesson'),project_id,nonempty(body.get('title'),'title',1000),refs)
        try:
            with store.tx():core.record_lesson(lesson,actor_id=principal.id)
        except (ValueError,PermissionError) as exc:
            bad(str(exc),400)
        return asdict(lesson)

    @api.post('/api/v1/projects/{project_id}/infra/bank/progress',status_code=201)
    def create_bank_progress(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        progress=Progress(
            ident('progress'),project_id,nonempty(body.get('milestone'),'milestone',1000),
            nonempty(body.get('status'),'status',120),
            tuple(list_of_str(body.get('artifactRefs',[]),'artifactRefs')),
        )
        with store.tx():core.record_progress(progress,actor_id=principal.id)
        return asdict(progress)

"""Backend-only observability endpoints for the migrated Infra core.

No current React page depends on these routes; they are intentionally additive.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
import time
from typing import Any
from fastapi import Depends
from .common import logged_in, page, project, bad, nonempty, list_of_str, oneof
from .storage import ident
from infra.memory import MemoryItem, EvidenceItem
from infra.project_bank import Issue, Decision, Lesson, Progress
from infra.evidence_graph import evidence_chains
from infra.persistence.backup import create_backup, restore_backup, BackupError
from infra.frontier import FrontierAdvisorUnavailable


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
    def infra_replay(project_id:str,sequence:int|None=None,_:Any=Depends(logged_in)):
        project(store,project_id)
        state=core.replay(project_id,sequence)
        return asdict(state)

    @api.get('/api/v1/projects/{project_id}/infra/replay/compare')
    def infra_replay_compare(project_id:str,fromSequence:int,toSequence:int,_:Any=Depends(logged_in)):
        project(store,project_id)
        try:
            return core.replay_compare(project_id,fromSequence,toSequence)
        except ValueError as exc:
            bad(str(exc),400)

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

    @api.get('/api/v1/projects/{project_id}/infra/overview')
    def infra_overview(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        event_row=store.execute(
            "SELECT COUNT(*) count,MIN(sequence) first_sequence,MAX(sequence) last_sequence "
            "FROM events WHERE project_id=?",(project_id,)
        ).fetchone()
        event_count=int(event_row['count'] or 0)
        first_sequence=event_row['first_sequence'];last_sequence=event_row['last_sequence']
        expected=(int(last_sequence)-int(first_sequence)+1) if first_sequence is not None and last_sequence is not None else 0
        memory=store.execute(
            "SELECT COUNT(*) total,SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active,"
            "SUM(CASE WHEN status='superseded' THEN 1 ELSE 0 END) superseded "
            "FROM memories WHERE project_id=?",(project_id,)
        ).fetchone()
        invocations=store.execute(
            "SELECT COUNT(*) total,"
            "SUM(CASE WHEN status='UNKNOWN' THEN 1 ELSE 0 END) unknown_count,"
            "SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) failed_count,"
            "SUM(usage_input) input_tokens,SUM(usage_output) output_tokens "
            "FROM gateway_invocations WHERE project_id=?",(project_id,)
        ).fetchone()
        bank={
            'openIssues':int(store.execute(
                "SELECT COUNT(*) FROM bank_issues WHERE project_id=? AND status NOT IN ('RESOLVED','WONT_FIX','DUPLICATE')",
                (project_id,)).fetchone()[0]),
            'decisions':int(store.execute("SELECT COUNT(*) FROM bank_decisions WHERE project_id=?",(project_id,)).fetchone()[0]),
            'lessons':int(store.execute("SELECT COUNT(*) FROM bank_lessons WHERE project_id=?",(project_id,)).fetchone()[0]),
            'progress':int(store.execute("SELECT COUNT(*) FROM bank_progress WHERE project_id=?",(project_id,)).fetchone()[0]),
        }
        scheduler=core.scheduler_snapshot(project_id)
        outbox=store.execute(
            "SELECT COUNT(*) total,"
            "SUM(CASE WHEN published_at IS NULL AND dead_letter_at IS NULL THEN 1 ELSE 0 END) pending,"
            "SUM(CASE WHEN dead_letter_at IS NOT NULL THEN 1 ELSE 0 END) dead_letters "
            "FROM consistency_outbox WHERE project_id=?",(project_id,)
        ).fetchone()
        replay_ok=True
        replay_error=None
        try:
            core.replay(project_id)
        except Exception as exc:
            replay_ok=False;replay_error=str(exc)[:500]
        return {
            'events':{'count':event_count,'firstSequence':first_sequence,'lastSequence':last_sequence,
                      'sequenceGapCount':max(0,expected-event_count)},
            'scheduler':{'capacity':scheduler['capacity'],'active':scheduler['active'],
                         'queued':scheduler['queued'],'parked':scheduler['parked']},
            'memory':{'total':int(memory['total'] or 0),'active':int(memory['active'] or 0),
                      'superseded':int(memory['superseded'] or 0)},
            'bank':bank,
            'invocations':{'total':int(invocations['total'] or 0),'unknown':int(invocations['unknown_count'] or 0),
                           'failed':int(invocations['failed_count'] or 0),
                           'inputTokens':int(invocations['input_tokens'] or 0),
                           'outputTokens':int(invocations['output_tokens'] or 0)},
            'consistency':{'outboxTotal':int(outbox['total'] or 0),'outboxPending':int(outbox['pending'] or 0),
                           'deadLetters':int(outbox['dead_letters'] or 0)},
            'replay':{'healthy':replay_ok,'error':replay_error},
        }

    @api.get('/api/v1/projects/{project_id}/infra/scheduler')
    def infra_scheduler(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return core.scheduler_snapshot(project_id)

    @api.get('/api/v1/projects/{project_id}/infra/context')
    def infra_context(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        snapshots=store.execute(
            "SELECT body FROM objects WHERE kind='contextSnapshot' AND project_id=? ORDER BY rowid DESC LIMIT 100",
            (project_id,)
        ).fetchall()
        items=[]
        for row in snapshots:
            value=json.loads(row['body'])
            items.append({
                'id':value.get('id'),'agentId':value.get('agentId'),'taskId':value.get('taskId'),
                'channelId':value.get('channelId'),'projection':value.get('projection'),
                'artifactCount':len(value.get('artifactIds') or []),
                'taskRevision':value.get('taskRevision'),'createdAt':value.get('createdAt'),
                'providerCheckpointId':value.get('providerCheckpointId'),
                'memoryBundleId':value.get('memoryBundleId'),
                'memorySourceRefs':value.get('memorySourceRefs') or [],
                'memoryTokenCount':value.get('memoryTokenCount'),
                'contextAssemblyRunId':value.get('contextAssemblyRunId'),
            })
        histories=store.execute(
            "SELECT session_id,COUNT(*) entry_count,MIN(occurred_at) first_at,MAX(occurred_at) last_at "
            "FROM history WHERE project_id=? GROUP BY session_id ORDER BY last_at DESC",(project_id,)
        ).fetchall()
        runs=[]
        for row in store.execute(
            "SELECT * FROM context_assembly_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
            (project_id,)
        ).fetchall():
            value=dict(row)
            value['included']=json.loads(value.pop('included_json'))
            value['excluded']=json.loads(value.pop('excluded_json'))
            value['candidates']=json.loads(value.pop('candidates_json'))
            runs.append(value)
        scopes=[row[0] for row in store.execute(
            "SELECT DISTINCT role_scope FROM memories WHERE project_id=? ORDER BY role_scope",(project_id,)
        ).fetchall()]
        return {'snapshots':items,'historySessions':[dict(row) for row in histories],
                'assemblyRuns':runs,'memoryRoleScopes':scopes}

    @api.get('/api/v1/projects/{project_id}/infra/bank')
    def infra_bank(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        issues=[dict(row) for row in store.execute(
            "SELECT * FROM bank_issues WHERE project_id=? ORDER BY rowid DESC",(project_id,)).fetchall()]
        decisions=[dict(row) for row in store.execute(
            "SELECT * FROM bank_decisions WHERE project_id=? ORDER BY rowid DESC",(project_id,)).fetchall()]
        lessons=[]
        for row in store.execute("SELECT * FROM bank_lessons WHERE project_id=? ORDER BY rowid DESC",(project_id,)).fetchall():
            value=dict(row);value['source_refs']=list(filter(None,str(value.get('source_refs') or '').split('\n')));lessons.append(value)
        progress=[]
        for row in store.execute("SELECT * FROM bank_progress WHERE project_id=? ORDER BY rowid DESC",(project_id,)).fetchall():
            value=dict(row);value['artifact_refs']=list(filter(None,str(value.get('artifact_refs') or '').split('\n')));progress.append(value)
        return {'issues':issues,'decisions':decisions,'lessons':lessons,'progress':progress}

    @api.get('/api/v1/projects/{project_id}/infra/a2cm')
    def infra_a2cm(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return core.a2cm_snapshot(project_id)

    @api.post('/api/v1/projects/{project_id}/infra/a2cm/primary-session',status_code=201)
    def infra_a2cm_primary(project_id:str,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not principal.allows('approvals.decide',project_id):
            bad('Missing permission: approvals.decide',403)
        with store.tx():
            return core.ensure_a2cm_primary(project_id,actor_id=principal.id)

    @api.post('/api/v1/projects/{project_id}/infra/a2cm/observations',status_code=201)
    def infra_a2cm_observation(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not principal.allows('tasks.execute',project_id):
            bad('Missing permission: tasks.execute',403)
        try:
            confidence=float(body.get('confidence',0.5))
        except (TypeError,ValueError):
            bad('confidence must be numeric',400)
        source=str(body.get('source') or 'fast_tool')
        if source not in {'fast_tool','primary','frontier'}:
            bad('source must be fast_tool, primary or frontier',400)
        try:
            with store.tx():
                return core.record_a2cm_observation(
                    project_id=project_id,actor_id=principal.id,
                    content=nonempty(body.get('content'),'content',100000),
                    evidence_event_id=nonempty(body.get('evidenceEventId'),'evidenceEventId',160),
                    confidence=confidence,source=source,
                )
        except (ValueError,PermissionError) as exc:
            bad(str(exc),400)

    @api.post('/api/v1/projects/{project_id}/infra/a2cm/candidates/{candidate_id}/review')
    def infra_a2cm_review(project_id:str,candidate_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not principal.allows('approvals.decide',project_id):
            bad('Missing permission: approvals.decide',403)
        counter=tuple(list_of_str(body.get('counterevidence',[]),'counterevidence'))
        uncertainty=tuple(list_of_str(body.get('uncertainty',[]),'uncertainty'))
        if len(counter)>20 or len(uncertainty)>20:
            bad('counterevidence/uncertainty support at most 20 entries each',400)
        if any(len(item)>1000 for item in (*counter,*uncertainty)):
            bad('counterevidence/uncertainty entries must be at most 1000 characters',400)
        try:
            with store.tx():
                return core.review_a2cm_candidate(
                    project_id=project_id,candidate_id=candidate_id,
                    primary_session_id=nonempty(body.get('primarySessionId'),'primarySessionId',160),
                    decision=oneof(body.get('decision'),('PROMOTE','REJECT','DEFER'),'decision'),
                    policy_version=nonempty(body.get('policyVersion'),'policyVersion',160),
                    counterevidence=counter,uncertainty=uncertainty,
                    rationale=nonempty(body.get('rationale'),'rationale',4000),
                    actor_id=principal.id,
                )
        except KeyError as exc:
            bad(str(exc),404)
        except PermissionError as exc:
            bad(str(exc),403)
        except ValueError as exc:
            bad(str(exc),409)

    @api.get('/api/v1/projects/{project_id}/infra/frontier')
    def infra_frontier(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return core.frontier_snapshot(project_id)

    @api.post('/api/v1/projects/{project_id}/infra/frontier/consult',status_code=201)
    def infra_frontier_consult(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not principal.allows('tasks.execute',project_id):
            bad('Missing permission: tasks.execute',403)
        try:
            return core.frontier_consult(
                project_id=project_id,
                parent_task_id=nonempty(body.get('parentTaskId'),'parentTaskId',160),
                owner_session_id=nonempty(body.get('ownerSessionId'),'ownerSessionId',160),
                capability=nonempty(body.get('capability'),'capability',500),
                budget=body.get('budget'),
                actor_id=principal.id,
                operation_id=nonempty(body.get('operationId'),'operationId',128),
            )
        except FrontierAdvisorUnavailable as exc:
            bad(str(exc),503)
        except ValueError as exc:
            bad(str(exc),400)
        except RuntimeError as exc:
            bad(str(exc),409)

    @api.post('/api/v1/projects/{project_id}/infra/frontier/delegate',status_code=202)
    def infra_frontier_delegate(project_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not principal.allows('tasks.execute',project_id):
            bad('Missing permission: tasks.execute',403)
        commands=body.get('acceptanceCommands')
        if not isinstance(commands,list):
            bad('acceptanceCommands must be a list',400)
        try:
            return core.frontier_delegate(
                project_id=project_id,
                parent_task_id=nonempty(body.get('parentTaskId'),'parentTaskId',160),
                owner_session_id=nonempty(body.get('ownerSessionId'),'ownerSessionId',160),
                capability=nonempty(body.get('capability'),'capability',500),
                objective=nonempty(body.get('objective'),'objective',8000),
                acceptance_commands=commands,
                budget=body.get('budget'),
                actor_id=principal.id,
                operation_id=nonempty(body.get('operationId'),'operationId',128),
                confirmation=str(body.get('confirmation') or ''),
            )
        except PermissionError as exc:
            bad(str(exc),403)
        except ValueError as exc:
            bad(str(exc),400)
        except RuntimeError as exc:
            if 'not available' in str(exc):
                bad(str(exc),503)
            bad(str(exc),409)

    @api.post('/api/v1/projects/{project_id}/infra/frontier/delegations/{request_id}/cancel')
    def infra_frontier_cancel(project_id:str,request_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not principal.allows('tasks.execute',project_id):
            bad('Missing permission: tasks.execute',403)
        try:
            return core.frontier_cancel_delegate(
                project_id=project_id,request_id=request_id,actor_id=principal.id,
                rationale=nonempty(body.get('rationale'),'rationale',4000),
            )
        except KeyError as exc:
            bad(str(exc),404)
        except ValueError as exc:
            bad(str(exc),409)
        except RuntimeError as exc:
            bad(str(exc),503)

    @api.post('/api/v1/projects/{project_id}/infra/frontier/delegations/{request_id}/review')
    def infra_frontier_review(project_id:str,request_id:str,body:dict,principal:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not principal.allows('tasks.execute',project_id):
            bad('Missing permission: tasks.execute',403)
        try:
            return core.frontier_review_delegate(
                project_id=project_id,request_id=request_id,
                owner_session_id=nonempty(body.get('ownerSessionId'),'ownerSessionId',160),
                decision=nonempty(body.get('decision'),'decision',20),
                actor_id=principal.id,
                rationale=nonempty(body.get('rationale'),'rationale',4000),
            )
        except KeyError as exc:
            bad(str(exc),404)
        except PermissionError as exc:
            bad(str(exc),403)
        except ValueError as exc:
            bad(str(exc),409)
        except RuntimeError as exc:
            bad(str(exc),503)

    @api.get('/api/v1/projects/{project_id}/infra/provenance')
    def infra_provenance(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return evidence_chains(store.conn,project_id)

    @api.get('/api/v1/projects/{project_id}/infra/invocations')
    def infra_invocations(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        rows=store.execute(
            "SELECT idempotency_key,model,mode,invocation_id,fallback_reason,status,"
            "provider_request_id,usage_input,usage_output FROM gateway_invocations "
            "WHERE project_id=? ORDER BY rowid DESC LIMIT 200",(project_id,)
        ).fetchall()
        return page([dict(row) for row in rows])

    @api.get('/api/v1/projects/{project_id}/infra/consistency')
    def infra_consistency(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        outbox=[dict(row) for row in store.execute(
            "SELECT message_id,aggregate_type,aggregate_id,aggregate_version,event_type,"
            "attempt_count,published_at,last_error,dead_letter_at,dead_letter_reason "
            "FROM consistency_outbox WHERE project_id=? ORDER BY rowid DESC LIMIT 100",(project_id,)
        ).fetchall()]
        aggregates=[dict(row) for row in store.execute(
            "SELECT aggregate_type,aggregate_id,state,version FROM consistency_aggregates "
            "WHERE project_id=? ORDER BY rowid DESC LIMIT 100",(project_id,)
        ).fetchall()]
        scheduler=core.scheduler_snapshot(project_id)
        session_ids={item['sessionId'] for item in scheduler['sessions']}
        leases=[]
        if session_ids:
            placeholders=','.join('?' for _ in session_ids)
            rows=store.execute(
                f"SELECT resource_id,holder_id,fencing_token,expires_at FROM consistency_leases "
                f"WHERE holder_id IN ({placeholders}) ORDER BY expires_at DESC",tuple(session_ids)
            ).fetchall()
            leases=[dict(row) for row in rows]
        sagas=[]
        for row in store.execute("SELECT saga_id,saga_type,state,payload FROM consistency_sagas ORDER BY rowid DESC LIMIT 200").fetchall():
            payload=json.loads(row['payload'])
            scoped=payload.get('project_id') or payload.get('projectId')
            if scoped==project_id:
                sagas.append({'sagaId':row['saga_id'],'sagaType':row['saga_type'],'state':row['state'],'payload':payload})
        inbox_count=0
        if outbox:
            message_ids=[item['message_id'] for item in outbox]
            placeholders=','.join('?' for _ in message_ids)
            inbox_count=int(store.execute(
                f"SELECT COUNT(*) FROM consistency_inbox WHERE message_id IN ({placeholders})",tuple(message_ids)
            ).fetchone()[0])
        return {'aggregates':aggregates,'outbox':outbox,'inboxCount':inbox_count,
                'leases':leases,'sagas':sagas}

    @api.get('/api/v1/infra/backups')
    def infra_backups(_:Any=Depends(logged_in)):
        rows=store.execute(
            "SELECT id,kind,status,backup_id,file_count,checksum_status,error,created_at,completed_at "
            "FROM backup_runs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return page([{**dict(row),'storage_ref':row['backup_id']} for row in rows])

    @api.post('/api/v1/infra/backups',status_code=201)
    def infra_create_backup(_:Any=Depends(logged_in)):
        backup_root=Path(api.state.backup_dir)
        backup_root.mkdir(parents=True,exist_ok=True)
        backup_id=ident('backup')
        run_id=ident('backuprun')
        destination=backup_root/backup_id
        created=datetime.now(timezone.utc).isoformat()
        store.execute(
            "INSERT INTO backup_runs(id,kind,status,backup_id,destination,created_at) VALUES(?,?,?,?,?,?)",
            (run_id,'BACKUP','RUNNING',backup_id,str(destination),created)
        )
        try:
            artifact_root=Path(api.state.artifact_dir)
            artifact_dirs={'artifacts':artifact_root} if artifact_root.is_dir() else {}
            create_backup(
                destination,
                databases={'mrlc':Path(core.database_path)},
                artifact_dirs=artifact_dirs,
            )
            manifest=destination/'manifest.json'
            payload=json.loads(manifest.read_text())
            file_count=len(payload.get('files',[]))
            completed=datetime.now(timezone.utc).isoformat()
            store.execute(
                "UPDATE backup_runs SET status='COMPLETED',manifest_path=?,file_count=?,"
                "checksum_status='VERIFIED',completed_at=? WHERE id=?",
                (str(manifest),file_count,completed,run_id)
            )
            store.audit('backup',f'Backup {backup_id} created and checksummed',level='info')
            store.emit('infra.backup.completed',None,{'backupId':backup_id,'runId':run_id})
            return {'id':run_id,'backupId':backup_id,'status':'COMPLETED','fileCount':file_count,
                    'checksumStatus':'VERIFIED','createdAt':created,'completedAt':completed}
        except Exception as exc:
            completed=datetime.now(timezone.utc).isoformat()
            store.execute(
                "UPDATE backup_runs SET status='FAILED',error=?,completed_at=? WHERE id=?",
                (str(exc)[:3000],completed,run_id)
            )
            store.audit('backup',f'Backup {backup_id} failed: {exc}',level='error')
            bad(f'Backup failed: {exc}',500)

    @api.post('/api/v1/infra/backups/{backup_id}/verify')
    def infra_verify_backup(backup_id:str,_:Any=Depends(logged_in)):
        source=store.execute(
            "SELECT * FROM backup_runs WHERE backup_id=? AND kind='BACKUP' AND status='COMPLETED' "
            "ORDER BY created_at DESC LIMIT 1",(backup_id,)
        ).fetchone()
        if source is None:
            bad('Backup not found',404)
        backup_root=Path(api.state.backup_dir)
        drill_id=ident('verify')
        run_id=ident('backuprun')
        destination=backup_root/'verify-drills'/drill_id
        created=datetime.now(timezone.utc).isoformat()
        store.execute(
            "INSERT INTO backup_runs(id,kind,status,backup_id,destination,created_at) VALUES(?,?,?,?,?,?)",
            (run_id,'VERIFY','RUNNING',backup_id,str(destination),created)
        )
        try:
            result=restore_backup(Path(source['destination']),destination)
            completed=datetime.now(timezone.utc).isoformat()
            store.execute(
                "UPDATE backup_runs SET status='COMPLETED',manifest_path=?,file_count=?,"
                "checksum_status='VERIFIED',completed_at=? WHERE id=?",
                (str(result.manifest_path),result.files,completed,run_id)
            )
            shutil.rmtree(destination,ignore_errors=True)
            store.audit('backup',f'Backup {backup_id} restore drill verified',level='info')
            store.emit('infra.backup.verified',None,{'backupId':backup_id,'runId':run_id})
            return {'id':run_id,'backupId':backup_id,'status':'COMPLETED',
                    'fileCount':result.files,'checksumStatus':'VERIFIED',
                    'createdAt':created,'completedAt':completed}
        except Exception as exc:
            completed=datetime.now(timezone.utc).isoformat()
            shutil.rmtree(destination,ignore_errors=True)
            store.execute(
                "UPDATE backup_runs SET status='FAILED',error=?,completed_at=? WHERE id=?",
                (str(exc)[:3000],completed,run_id)
            )
            store.audit('backup',f'Backup {backup_id} restore drill failed: {exc}',level='error')
            bad(f'Backup verification failed: {exc}',500)

    @api.get('/api/v1/projects/{project_id}/infra/operations')
    def infra_operations(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        checkpoint=core.continuity.latest(project_id)
        tools=[]
        for row in store.execute("SELECT tool_id,name,roles,projects,version,credential_ref FROM mcp_tools ORDER BY tool_id").fetchall():
            projects=set(filter(None,str(row['projects']).split(',')))
            if projects and project_id not in projects:continue
            tools.append({'toolId':row['tool_id'],'name':row['name'],'version':row['version'],
                          'credentialConfigured':bool(row['credential_ref'])})
        skills=[]
        for row in store.execute("SELECT skill_id,version,capabilities,roles,projects FROM skills ORDER BY skill_id,version").fetchall():
            projects=set(filter(None,str(row['projects']).split(',')))
            if projects and project_id not in projects:continue
            skills.append({'skillId':row['skill_id'],'version':row['version'],
                           'capabilities':list(filter(None,str(row['capabilities']).split(',')))})
        workers=[dict(row) for row in store.execute(
            "SELECT wr.handle,wr.adapter,wr.session_id,wr.task_id,wr.fencing_token,wr.status,"
            "wr.checkpoint_ref FROM worker_runs wr JOIN sessions s ON s.session_id=wr.session_id "
            "WHERE s.project_id=? ORDER BY wr.rowid DESC LIMIT 100",(project_id,)
        ).fetchall()]
        mcp_audit=[{
            'toolId':entry.tool_id,'roleId':entry.role_id,'result':entry.result,
            'recordedAt':entry.recorded_at.isoformat(),
        } for entry in core.mcp.access_log(project_id)[-100:]]
        latest_backup=store.execute(
            "SELECT id,kind,status,backup_id,file_count,checksum_status,error,created_at,completed_at "
            "FROM backup_runs WHERE kind='BACKUP' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_verify=store.execute(
            "SELECT id,kind,status,backup_id,file_count,checksum_status,error,created_at,completed_at "
            "FROM backup_runs WHERE kind='VERIFY' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return {
            'continuity':None if checkpoint is None else {
                'checkpointId':checkpoint.checkpoint_id,'sessionId':checkpoint.session_id,
                'client':checkpoint.client,'gitBranch':checkpoint.git_branch,
                'gitHeadSha':checkpoint.git_head_sha,'gitDirty':checkpoint.git_dirty,
                'verificationStatus':checkpoint.verification_status,
                'blockers':list(checkpoint.blockers),'nextSteps':list(checkpoint.next_steps),
                'createdAt':checkpoint.created_at.isoformat(),
            },
            'mcp':{'tools':tools,'accessAudit':mcp_audit},
            'skills':skills,'workers':workers,
            'backup':{
                'tracked':True,
                'latestBackup':None if latest_backup is None else {**dict(latest_backup),'storage_ref':latest_backup['backup_id']},
                'latestVerify':None if latest_verify is None else {**dict(latest_verify),'storage_ref':latest_verify['backup_id']},
            },
        }


"""Backend-only observability endpoints for the migrated Infra core.

No current React page depends on these routes; they are intentionally additive.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from fastapi import Depends
from .common import logged_in, page, project


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

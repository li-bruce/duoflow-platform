"""Authenticated platform controls; business events go directly to the center."""
import json
import re
from fastapi import APIRouter, Depends, Request
from .errors import PlatformError, require
from .identity import can_manage_platform, refresh_actor


async def bounded_json(request, maximum):
    data=bytearray()
    async for chunk in request.stream():
        require(len(data)+len(chunk)<=maximum,'coordination_size','协同配置内容过大。',413)
        data.extend(chunk)
    try:
        return json.loads(data,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):
        raise PlatformError('coordination_config','需要有效的协同配置。',422) from None


def create_coordination_router(client,runtime,require_admin,check_csrf):
    router=APIRouter(prefix='/api/v1/coordination',tags=['coordination'])
    def current_admin(actor):
        with runtime.sessions() as db:
            current=refresh_actor(db,actor,require_unchanged=True)
        require(can_manage_platform(current),'forbidden','业务协同配置与公开审批需要平台管理权限。',403)
        return current

    @router.get('/summary')
    async def summary(actor=Depends(require_admin)):
        current_admin(actor)
        value=await client.summary()
        current_admin(actor)
        return value

    @router.get('/configuration')
    def configuration(actor=Depends(require_admin)):
        current_admin(actor)
        return {**client.settings(),'modules':client.modules()}

    @router.put('/configuration',dependencies=[Depends(check_csrf)])
    async def configure(request:Request,actor=Depends(require_admin)):
        current_admin(actor)
        value=await bounded_json(request,65536)
        result=await client.configure(value,lambda:current_admin(actor))
        current_admin(actor)
        return result

    @router.post('/approvals/{event_id}/decide',dependencies=[Depends(check_csrf)])
    async def decide(event_id:str,request:Request,actor=Depends(require_admin)):
        current_admin(actor)
        value=await bounded_json(request,1024)
        require(isinstance(value,dict) and set(value)=={'expected_revision','decision'} and
                value['decision'] in ('approved','rejected') and type(value['expected_revision']) is int and value['expected_revision']>=1,
                'coordination_decision','请明确选择同意或退回。',422)
        require(re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',event_id) is not None,
                'coordination_event','申请编号无效。',422)
        await client.reconcile()
        current=current_admin(actor)
        result=await client.request('POST','/approvals/'+event_id+'/decide',
                                    {**value,'actor_id':current['id']})
        current_admin(actor)
        return result
    return router

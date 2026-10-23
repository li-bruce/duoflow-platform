"""Host contract tests with synthetic native and remote-style capabilities.

Header actors are a test harness; production routes use the shared session/CSRF dependencies.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.contracts import CapabilityOutput
from app.conversation import ConversationService
from app.conversation_routes import create_conversation_router
from app.db import IdentityProfile, InteractionInstance, Invocation, User, create_database, user_dict
from app.errors import PlatformError
from app.interaction_routes import create_interaction_router
from app.interactions import Interactions
from app.runtime import Runtime


ROOT = Path(__file__).resolve().parents[1]
BASE = '/api/v1/interactions'
STATE = {'type':'object','additionalProperties':False,'required':['values'], 'properties':{'values':{'type':'array','items':{'type':'integer','minimum':0,'maximum':100},'maxItems':12}}}
DEFINITION = {'protocol_version':'1.0','id':'test.selector','version':'1.0.0','title':'合成选择器','description':'合成记录选择与汇总。',
 'triggers':['打开合成选择器'],'input_schema':{'type':'object','properties':{},'additionalProperties':False},'state_schema':STATE,'initial_state':{'values':[]},
 'actions':[{'id':'sum','title':'汇总','capability_id':'test.sum','capability_version':'1.0.0','completes':True,'bindings':{'values':{'source':'state','key':'values'}}}]}


@pytest.fixture
def host(tmp_path):
    (tmp_path/'extensions').mkdir(); (tmp_path/'extensions/installed.json').write_text('{"modules": []}')
    shutil.copytree(ROOT/'config',tmp_path/'config')
    pack=tmp_path/'components/packs/selector'; pack.mkdir(parents=True)
    (pack/'interaction.json').write_text(json.dumps(DEFINITION),encoding='utf-8')
    (tmp_path/'components/installed.json').write_text('{"packs":["selector"]}')
    engine,sessions=create_database(tmp_path/'data')
    with sessions.begin() as db:
        users=[User(username=n,name=n,role='member',password_hash='unused') for n in ('alice','bob')]
        db.add_all(users);db.flush(); actors={u.username:user_dict(u) for u in users}
    gateway=SimpleNamespace(configured=False,redact=lambda text:text.replace('synthetic-known-key','[hidden]'))
    runtime=Runtime(tmp_path,sessions,gateway,data_dir=tmp_path/'data')
    calls=[]
    def handler(ctx,payload):
        calls.append(deepcopy(payload))
        if payload['values']==[99]: raise PlatformError('synthetic_failure','合成失败',422)
        return CapabilityOutput('已完成合成汇总。',{'count':len(payload['values']),'total':sum(payload['values'])})
    module={'id':'test','version':'1.0.0','name':'test','description':'synthetic','source':'local'}
    runtime.registry.modules.append(module)
    spec={'id':'test.sum','version':'1.0.0','title':'合成汇总','description':'汇总值','kind':'query','input_schema':STATE,'output_schema':{'type':'object'},
          'roles':['member'],'execution':{'mode':'sync','confirmation':False},'presentation':{p:{'renderer':'json'} for p in ('page','conversation')}}
    runtime.registry.register(module,spec,handler)
    service=runtime.interactions
    conversations=ConversationService(runtime)
    app=FastAPI()
    def actor(x_actor: str=Header(default='alice')): return actors[x_actor]
    @app.exception_handler(PlatformError)
    async def error(request,exc): return JSONResponse({'error':{'code':exc.code,'message':exc.message}},status_code=exc.status)
    app.include_router(create_interaction_router(service,actor,actor))
    app.include_router(create_conversation_router(conversations,actor,actor))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client,runtime=runtime,service=service,sessions=sessions,actors=actors,calls=calls,conversations=conversations)
    engine.dispose()


def create(host,key='one',**fields):
    response=host.client.post(BASE+'/instances',json={'component_id':'test.selector','input':{},'idempotency_key':key,**fields})
    assert response.status_code==200,response.text
    return response.json()


def edit(host,item,values):
    return host.client.patch(BASE+'/instances/'+item['id'],json={'state':{'values':values},'expected_revision':item['revision']})


def action(host,item,key='act',action_id='sum'):
    return host.client.post(f"{BASE}/instances/{item['id']}/actions/{action_id}",json={'expected_revision':item['revision'],'idempotency_key':key})


def test_catalog_and_native_action_are_registered_and_persistent(host):
    assert host.client.get(BASE+'/catalog').json()['items'][0]['available']
    item=create(host)
    edited=edit(host,item,[2,3]).json()
    response=action(host,edited);assert response.status_code==200,response.text
    result=response.json()['instance']
    assert result['result']['data']=={'count':2,'total':5}
    assert result['status']=='completed' and result['revision']==3
    restarted=Interactions(host.runtime)
    assert restarted.get(host.actors['alice'],item['id'])['result']==result['result']
    assert host.calls==[{'values':[2,3]}]


def test_creation_and_action_retry_are_idempotent(host):
    item=create(host); assert create(host)['id']==item['id']
    result=action(host,item).json()
    retry=action(host,item)
    assert retry.status_code==200,retry.text
    repeated=retry.json()
    assert repeated['result']['id']==result['result']['id'] and len(host.calls)==1
    wrong=action(host,{**item,'revision':2})
    assert wrong.status_code==409


def test_stale_edit_and_stale_action_do_not_overwrite(host):
    item=create(host); assert edit(host,item,[1]).status_code==200
    assert edit(host,item,[2]).status_code==409
    assert action(host,item).status_code==409
    assert not host.calls


def test_same_component_instances_have_separate_state(host):
    a,b=create(host),create(host,'two');edit(host,a,[2])
    assert host.service.get(host.actors['alice'],b['id'])['state']=={'values':[]}


@pytest.mark.parametrize('verb,suffix,payload',[('get','',None),('patch','',{'state':{'values':[1]},'expected_revision':1}),('post','/actions/sum',{'expected_revision':1,'idempotency_key':'x'}),('post','/attach',{})])
def test_other_owner_cannot_read_modify_invoke_or_attach(host,verb,suffix,payload):
    item=create(host)
    args={'headers':{'X-Actor':'bob'}}
    if payload is not None:args['json']=payload
    assert getattr(host.client,verb)(BASE+'/instances/'+item['id']+suffix,**args).status_code==403
    assert host.client.get(BASE+'/instances',headers={'X-Actor':'bob'}).json()=={'items':[]}


@pytest.mark.parametrize('state',[{'values':['1']},{'values':[1],'role':'admin'},{'values':[101]},{'values':[1],'content_base64':'AAAA'}])
def test_state_schema_rejects_invalid_or_undeclared_fields(host,state):
    item=create(host)
    assert host.client.patch(BASE+'/instances/'+item['id'],json={'state':state,'expected_revision':1}).status_code==422


def test_action_cannot_choose_a_new_capability_or_supply_payload(host):
    item=create(host)
    assert action(host,item,action_id='unknown').status_code==422
    response=host.client.post(BASE+'/instances/'+item['id']+'/actions/sum',json={'expected_revision':1,'idempotency_key':'x','input':{'values':[88]}})
    assert response.status_code==422 and not host.calls


def test_failure_rolls_back_instance_and_invocation(host):
    item=edit(host,create(host),[99]).json()
    assert action(host,item).status_code==422
    assert host.service.get(host.actors['alice'],item['id'])['revision']==item['revision']
    with host.sessions() as db: assert db.query(Invocation).count()==0


def test_page_attach_chat_restore_and_fork_are_one_then_separate_instances(host):
    item=edit(host,create(host),[2,3]).json()
    response=host.client.post(BASE+'/instances/'+item['id']+'/attach').json()
    cid=response['conversation_id']
    assert host.client.post(BASE+'/instances/'+item['id']+'/attach').json()==response
    conv=host.client.get('/api/v1/conversations/'+cid).json()
    assert len(conv['messages'])==1 and conv['messages'][0]['views'][0]['data']['instance_id']==item['id']
    fork=host.client.post('/api/v1/conversations/'+cid+'/fork',json={'message_id':conv['messages'][0]['id']}).json()
    cloned_id=fork['messages'][0]['views'][0]['data']['instance_id']
    assert cloned_id!=item['id']
    cloned=host.service.get(host.actors['alice'],cloned_id)
    assert cloned['state']=={'values':[2,3]} and cloned['forked_from']==item['id']
    edit(host,cloned,[4])
    assert host.service.get(host.actors['alice'],item['id'])['state']=={'values':[2,3]}
    assert not host.calls


def test_explicit_prompt_creates_interaction_without_business_execution(host):
    conv=host.conversations.create(host.actors['alice'])
    response=asyncio.run(host.conversations.send(host.actors['alice'],conv['id'],'/component test.selector','offline','msg'))
    view=response['message']['views'][0]
    assert view['type']=='interaction' and not host.calls
    again=asyncio.run(host.conversations.send(host.actors['alice'],conv['id'],'/component test.selector','offline','msg'))
    assert again==response


def test_module_unpublication_and_definition_upgrade_block_old_instances(host):
    item=create(host)
    host.service.definitions['test.selector']['definition_hash']='changed'
    assert host.client.get(BASE+'/instances/'+item['id']).status_code==409
    host.runtime.registry.capabilities.pop('test.sum')
    assert host.client.get(BASE+'/catalog').json()['items']==[]


def test_identity_change_revokes_saved_instance(host):
    item=create(host)
    with host.sessions.begin() as db: db.add(IdentityProfile(user_id=host.actors['alice']['id'],roles=['member'],authorization_revision=2))
    assert host.client.get(BASE+'/instances/'+item['id']).status_code==403


def test_restore_completed_card_does_not_repeat_action(host):
    item=create(host);action(host,item)
    for _ in range(3):assert host.client.get(BASE+'/instances/'+item['id']).status_code==200
    assert len(host.calls)==1


def test_background_microservice_action_projects_worker_result_and_blocks_edits(host):
    host.runtime.registry.capabilities['test.sum']['execution']['mode']='background'
    item=edit(host,create(host),[4,5]).json()
    response=action(host,item).json()['instance']
    assert response['status']=='queued' and response['run_id']
    assert edit(host,response,[6]).status_code==409
    asyncio.run(host.runtime.process_run(response['run_id']))
    finished=host.service.get(host.actors['alice'],item['id'])
    assert finished['status']=='completed' and finished['result']['data']['total']==9
    assert edit(host,finished,[7]).status_code==200


def test_background_failure_can_retry_without_resubmitting_component(host):
    host.runtime.registry.capabilities['test.sum']['execution']['mode']='background'
    item=edit(host,create(host),[99]).json()
    result=action(host,item).json()['instance']
    asyncio.run(host.runtime.process_run(result['run_id']))
    assert host.service.get(host.actors['alice'],item['id'])['status']=='failed'
    asyncio.run(host.runtime.control_run(host.actors['alice'],result['run_id'],'retry'))
    assert host.service.get(host.actors['alice'],item['id'])['status']=='queued'


def test_model_tool_selection_opens_registered_component_without_running_business(host):
    async def chat(messages,profile_id=None,tools=None):
        tool=next(t for t in tools if '打开共享组件' in t['function']['description'])
        return {'content':'请在选择器中确认。','tool_calls':[{'function':{'name':tool['function']['name'],'arguments':json.dumps({'component_id':'test.selector','initial_input':{}})}}]}
    host.runtime.gateway.configured=True;host.runtime.gateway.chat=chat
    conv=host.conversations.create(host.actors['alice'])
    response=asyncio.run(host.conversations.send(host.actors['alice'],conv['id'],'请给我一个合成选择器来挑选记录',None,'model-test'))
    assert response['message']['views'][0]['type']=='interaction'
    assert not host.calls


def test_unregistered_component_selected_by_model_is_rejected(host):
    async def chat(messages,profile_id=None,tools=None):
        tool=next(t for t in tools if '打开共享组件' in t['function']['description'])
        return {'content':'请选择。','tool_calls':[{'function':{'name':tool['function']['name'],'arguments':'{"component_id":"unregistered.component"}'}}]}
    host.runtime.gateway.configured=True;host.runtime.gateway.chat=chat
    conv=host.conversations.create(host.actors['alice'])
    response=asyncio.run(host.conversations.send(host.actors['alice'],conv['id'],'打开一个共享组件',None,'invalid-model-test'))
    assert response['message']['views']==[] and not host.calls
    with host.sessions() as db: assert db.query(InteractionInstance).count()==0

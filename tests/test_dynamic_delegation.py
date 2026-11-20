import asyncio
import pytest
from agent_ecosystem.dynamic_delegation import AgentDelegationRegistry

class DummyMCP:
    async def refresh(self): return {}
    def list_for_agent(self, role): return []
    async def call(self, agent_id, tool_id, arguments):
        return {"structured_content": {"ok": True}}

@pytest.fixture
def cfg(tmp_path):
    p=tmp_path/'agents.yaml'
    p.write_text('''agents:
  research:
    url: http://127.0.0.1:8001
    description: Research
    capabilities: [web_search]
  coding:
    url: http://127.0.0.1:8002
    description: Coding
    capabilities: [python_debugging]
  analysis:
    url: http://127.0.0.1:8003
    description: Analysis
    capabilities: [statistics]
policy:
  max_hops: 2
  max_delegations_per_task: 2
  timeout_seconds: 10
''', encoding='utf8')
    return p

def names(reg, role):
    return [x['tool_id'] for x in reg.list_for_agent(role)]

def test_any_agent_can_discover_and_delegate(cfg):
    r=AgentDelegationRegistry(DummyMCP(), cfg)
    assert 'a2a:delegate_task' in names(r,'analysis')
    assert 'a2a:delegate_task' in names(r,'coding')
    assert 'a2a:discover_agents' in names(r,'research')

def test_route_prevents_ping_pong(cfg):
    r=AgentDelegationRegistry(DummyMCP(), cfg,route=['research','coding'])
    spec=next(x for x in r.list_for_agent('coding') if x['tool_id']=='a2a:delegate_task')
    assert spec['input_schema']['properties']['agent_id']['enum']==['analysis']
    with pytest.raises(Exception):
        asyncio.run(r.call('coding','a2a:delegate_task',{'agent_id':'research','task':'return'}))

def test_depth_stops_third_hop(cfg):
    r=AgentDelegationRegistry(DummyMCP(), cfg,route=['research','coding','analysis'])
    assert 'a2a:delegate_task' not in names(r,'analysis')

def test_metadata_propagated(cfg):
    found=[]
    async def sender(url, task, *, metadata):
        found.append((url,task,metadata))
        return 'done'
    r=AgentDelegationRegistry(DummyMCP(), cfg, sender=sender, route=['coding'])
    out=asyncio.run(r.call('coding','a2a:delegate_task',{'agent_id':'research','task':'find docs'}))
    assert out['structured_content']['result']=='done'
    assert found[0][2]['x-agent-delegation']['route']==['coding','research']

def test_reject_forged_route(cfg):
    with pytest.raises(ValueError,match='Repeated'):
        AgentDelegationRegistry(DummyMCP(),cfg,route=['research','coding','research'])

def test_budget(cfg):
    async def sender(url,task,*,metadata):return 'ok'
    r=AgentDelegationRegistry(DummyMCP(),cfg,sender=sender)
    asyncio.run(r.call('research','a2a:delegate_task',{'agent_id':'coding','task':'one'}))
    asyncio.run(r.call('research','a2a:delegate_task',{'agent_id':'analysis','task':'two'}))
    with pytest.raises(PermissionError):
        asyncio.run(r.call('research','a2a:delegate_task',{'agent_id':'coding','task':'three'}))

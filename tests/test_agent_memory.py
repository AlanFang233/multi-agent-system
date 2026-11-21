"""Offline, no HKGAI/MCP/A2A server needed: python -m pytest -q tests/test_agent_memory.py"""
import json
from types import SimpleNamespace

import pytest

from agent_ecosystem.memory.store import MemoryDB
from agent_ecosystem.memory.context import ContextManager, chars, grouped
from agent_ecosystem.memory.reflection import reflect
from agent_ecosystem.memory.runner import MemoryToolRegistry, run_tool_loop_with_memory


class DummyRegistry:
    def __init__(self, *, error=False):
        self.error=error
        self.calls=[]

    def list_for_agent(self,role):
        return [{
          "tool_id":"filesystem:list_directory",
          "description":"read test directory", "input_schema":{
            "type":"object","properties":{"path":{"type":"string"}},
            "required":["path"],"additionalProperties":False
          }
        }]

    async def call(self,agent_id,tool_id,arguments):
        self.calls.append((agent_id,tool_id,arguments))
        if self.error:
            return {"is_error":True,"structured_content":{"error":"Access denied - path outside allowed directories"}}
        return {"is_error":False,"structured_content":{"text":"XYZ"*4000}}


class FakeClient:
    def __init__(self, steps, raise_on=None):
        self.steps=list(steps)
        self.calls=[]
        self.raise_on=raise_on
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_on and len(self.calls)==self.raise_on:
            raise RuntimeError("simulated client crash")
        if not self.steps:
            raise AssertionError("too many LLM calls")
        next_step=self.steps.pop(0)
        if isinstance(next_step,str):
            msg=SimpleNamespace(content=next_step,tool_calls=[])
        else:
            name=next(x["function"]["name"] for x in kwargs["tools"]
                      if "filesystem_list_directory" in x["function"]["name"])
            batch=[SimpleNamespace(
                id=f"call-{len(self.calls)}-{i}",
                function=SimpleNamespace(name=name,arguments=json.dumps({"path":"."})))
                for i in range(next_step)]
            msg=SimpleNamespace(content=None,tool_calls=batch)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def config_file(tmp_path,max_chars=9000,trigger_chars=7000):
    # Budgets must fit the real SYSTEM_PROMPTS (role prompt + dynamic-skill policy
    # is ~3.6k chars); the old 3000/2300 defaults raised ContextLimitError before
    # the tested behavior could run.
    f=tmp_path/'memory.yaml'
    f.write_text(f'''
database: {tmp_path/'memory.sqlite'}
max_input_chars: {max_chars}
compact_trigger_chars: {trigger_chars}
keep_recent_groups: 2
summary_chars: 600
max_tool_preview_chars: 1100
memory_retrieval_limit: 3
''',encoding='utf-8')
    return f


def test_agent_namespace_and_review(tmp_path):
    db=MemoryDB(tmp_path/'db.sqlite')
    mid=db.put_memory('coding','procedural','Git 检查优先使用 git_status','t1',status='pending')
    assert db.search('coding','git 检查')==[]
    assert db.set_memory_status('research',mid,'verified') is False
    assert db.set_memory_status('coding',mid,'verified') is True
    assert db.search('coding','Git 仓库检查')[0]['memory_id']==mid
    assert db.search('research','Git 仓库检查')==[]
    assert (tmp_path/'db.sqlite').stat().st_mode & 0o777 == 0o600


def test_tool_result_isolation(tmp_path):
    db=MemoryDB(tmp_path/'db.sqlite')
    rid,_=db.save_result('coding','t-c','git:git_status','abcdef'*1000)
    assert db.read_result('coding','t-c',rid,100,40)['text']==('abcdef'*1000)[100:140]
    with pytest.raises(PermissionError):
        db.read_result('analysis','t-c',rid)
    with pytest.raises(PermissionError):
        db.read_result('coding','another-task',rid)


def test_compaction_preserves_complete_tool_pairs():
    ctx=ContextManager(max_input_chars=2200,compact_trigger_chars=1200,keep_recent_groups=1,summary_chars=300)
    msgs=[{'role':'system','content':'Rule'},{'role':'user','content':'Goal'}]
    for i in range(6):
        msgs.extend([
            {'role':'assistant','content':None,'tool_calls':[{'id':f'{i}','type':'function','function':{'name':'tool_test','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':f'{i}','content':'large-data'*40},
        ])
    kept,summary,compact=ctx.ensure(msgs,'',[])
    assert compact
    assert len(kept)<len(msgs)
    assert summary
    assert len(grouped(kept[2:]))>=1
    assert chars(ctx.for_model(kept,summary))<=ctx.max_input_chars


def test_invalid_partial_tool_group():
    with pytest.raises(ValueError):
        grouped([{'role':'assistant','tool_calls':[{'id':'missing'}]}])


@pytest.mark.asyncio
async def test_actual_loop_compacts_and_stores_raw_result(tmp_path):
    client=FakeClient([1]*9+['finished'])
    registry=DummyRegistry()
    answer,used=await run_tool_loop_with_memory(
        role='coding',prompt='请检查文件状态',registry=registry,client=client,model='fake',
        max_rounds=10,task_id='t1',config_path=config_file(tmp_path,max_chars=8000,trigger_chars=4000))
    assert answer=='finished' and used==['filesystem:list_directory']*9
    db=MemoryDB(tmp_path/'memory.sqlite')
    ev=db.events('coding','t1')
    assert any(e['event_type']=='context_compacted' for e in ev)
    assert db.stats()['results']==9
    assert all(chars(c['messages'])+chars(c.get('tools',[]))<=8000 for c in client.calls)
    assert db.task('coding','t1')['status']=='completed'
    with db.connection() as conn:
        rid=conn.execute('SELECT result_id FROM tool_results LIMIT 1').fetchone()[0]
    raw=db.read_result('coding','t1',rid,0,1000)
    assert raw['total_chars']>1000
    # Retry a completed task must not invoke the model again.
    again=FakeClient([])
    assert (await run_tool_loop_with_memory(role='coding',prompt='请检查文件状态',registry=registry,
        client=again,model='fake',task_id='t1',config_path=config_file(tmp_path,max_chars=8000,trigger_chars=4000)))[0]=='finished'
    assert again.calls==[]


@pytest.mark.asyncio
async def test_safe_auto_learning_and_retrieval(tmp_path):
    cfg=config_file(tmp_path)
    await run_tool_loop_with_memory(role='coding',prompt='文件权限检查',registry=DummyRegistry(error=True),
        client=FakeClient([1,'finished']),model='fake',task_id='t1',config_path=cfg)
    db=MemoryDB(tmp_path/'memory.sqlite')
    matches=db.search('coding','文件系统权限 Access denied')
    assert matches and all(m['status']=='verified' for m in matches)
    assert db.search('research','文件系统权限 Access denied')==[]
    other=FakeClient(['finished another'])
    await run_tool_loop_with_memory(role='coding',prompt='文件系统权限 Access denied 怎么办',registry=DummyRegistry(),
        client=other,model='fake',task_id='t2',config_path=cfg)
    assert any('长期记忆检索结果' in str(x.get('content')) for x in other.calls[0]['messages'])


@pytest.mark.asyncio
async def test_resume_from_completed_checkpoint(tmp_path):
    cfg=config_file(tmp_path)
    reg=DummyRegistry()
    with pytest.raises(RuntimeError,match='simulated'):
        await run_tool_loop_with_memory(role='coding',prompt='recover',registry=reg,
            client=FakeClient([1],raise_on=2),model='fake',task_id='crash',config_path=cfg)
    db=MemoryDB(tmp_path/'memory.sqlite')
    assert db.task('coding','crash')['status']=='failed'
    assert db.load_checkpoint('coding','crash') is not None
    after=FakeClient(['recovered'])
    answer,used=await run_tool_loop_with_memory(role='coding',prompt='recover',registry=reg,
        client=after,model='fake',task_id='crash',config_path=cfg)
    assert answer=='recovered'
    assert len(reg.calls)==1
    assert used==['filesystem:list_directory']


def test_successful_tool_produces_verified_route(tmp_path):
    db=MemoryDB(tmp_path/'db.sqlite')
    db.begin('analysis','t1','统计 loss')
    db.event('analysis','t1','tool_result',{'tool_id':'skills:loss_metrics','is_error':False,'preview':'result computed'})
    added=reflect(db,'analysis','t1')
    assert len(added)==1
    assert db.search('analysis','loss 统计')
    assert not db.search('coding','loss 统计')


@pytest.mark.asyncio
async def test_memory_read_result_tool_is_task_scoped(tmp_path):
    db=MemoryDB(tmp_path/'db.sqlite')
    rid,_=db.save_result('research','t1','filesystem:read_file','hello'*1000)
    layer=MemoryToolRegistry(DummyRegistry(),db,'research','t1')
    okay=await layer.call('research','memory:read_result',{'result_id':rid,'start':5,'length':20})
    assert okay['structured_content']['text']==('hello'*1000)[5:25]
    with pytest.raises(PermissionError):
        await layer.call('coding','memory:read_result',{'result_id':rid})
    other_layer=MemoryToolRegistry(DummyRegistry(),db,'research','t2')
    with pytest.raises(PermissionError):
        await other_layer.call('research','memory:read_result',{'result_id':rid})

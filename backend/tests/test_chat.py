import json
import hashlib
import pytest
from sqlalchemy import select, func
from app.chat import process_chat, report_graph, checked_answer, Answer, quick_queries
from app.db import Session, ChatTask, Conversation, Artifact, Message, Step, Run
from app.worker import recover
from app.domain import parse_pages
from app.providers import ProviderError
from tests.fixtures import FakeProviders, PROFILE, plan, raw_page


class ChatProviders(FakeProviders):
    def __init__(self, empty=False, queries=None):
        super().__init__(empty)
        self.queries = queries or []

    async def search(self, query):
        self.searches.append(query)
        return {'Pages': [], 'RequestId': 'test-empty'} if self.empty else raw_page(title='拌饭探店体验')

    async def llm(self, messages):
        system = messages[0]['content']
        data = json.loads(messages[1]['content'])
        if '判断用户当前问题' in system:
            result = {'queries': self.queries}
        elif '独立审核候选回答' in system:
            result = {'supported_indices': list(range(len(data['blocks'])))}
        elif '分析这条需求查询下' in system:
            result = {'clues': [{'evidence_id': e['evidence_id'], 'merchants': ['老王拌饭'], 'angles': ['拌饭'],
                'citations': [{'evidence_id': e['evidence_id'], 'quote': e['passage']}]} for e in data['evidence']]}
        elif 'blocks' in system:
            e = data['evidence']
            result = {'blocks': [{'kind': 'fact', 'text': '深圳市有老王拌饭的公开提及。', 'citations': [{'evidence_id': e[0]['evidence_id'], 'quote': e[0]['passage']}]}]} if e else {'blocks': [{'kind': 'uncertainty', 'text': '本次未查到可靠来源。', 'citations': []}]}
        else:
            return await super().llm(messages)
        self.messages.append(messages)
        return {'data': result, 'model': 'fixture-only'}


def start(client):
    cid = client.post('/api/v1/conversations').json()['id']
    r = client.post(f'/api/v1/conversations/{cid}/messages', headers={'Idempotency-Key': 'discovery-test'}, json={'kind':'discovery', 'profile': PROFILE.model_dump()})
    assert r.status_code == 202, r.text
    return cid, r.json()['id']


def send(client, cid, **body):
    return client.post(f'/api/v1/conversations/{cid}/messages', json=body, headers={'Idempotency-Key': hashlib.sha256(json.dumps(body,ensure_ascii=False).encode()).hexdigest()})


def artifact(client, cid):
    c = client.get(f'/api/v1/conversations/{cid}').json()
    return client.get('/api/v1/artifacts/'+c['artifacts'][-1]['id']).json()


def test_first_message_and_auth(client):
    cid = client.post('/api/v1/conversations').json()['id']
    assert send(client,cid,text='hello').status_code == 422
    assert send(client,cid,kind='discovery',profile={'name':'a'}).status_code == 422
    client.headers.pop('Authorization')
    assert client.get('/api/v1/conversations').status_code == 401


@pytest.mark.asyncio
async def test_discovery_idempotency_snapshot_and_graph(client):
    cid, tid = start(client)
    assert send(client,cid,text='等待').status_code == 409
    repeated = client.post(f'/api/v1/conversations/{cid}/messages',headers={'Idempotency-Key':'discovery-test'},json={'kind':'discovery','profile':PROFILE.model_dump()})
    assert repeated.json()['id'] == tid
    fake=ChatProviders()
    await process_chat(tid,fake)
    a=artifact(client,cid);r=a['report']
    assert r['status']=='completed'
    assert len(fake.searches)==6 and len(r['scenarios'])==6
    assert 'metrics' not in r
    assert [s['search_kind'] for s in r['scenarios']].count('brand')==1
    assert all(PROFILE.name not in q for q in fake.searches[:5])
    assert r['exposure']['mentioned_queries']==5
    assert not any('提取搜索证据中同城' in m[0]['content'] for m in fake.messages)
    graph=client.get('/api/v1/artifacts/'+a['id']+'/graph').json()
    ids={n['id'] for n in graph['nodes']}
    evidence={p['id'] for s in r['scenarios'] for p in s['pages']}
    assert graph['links']
    for link in graph['links']:
        assert link['source'] in ids and link['target'] in ids
        assert set(link['evidence_ids']) <= evidence
    assert client.get('/api/v1/artifacts/'+a['id']+'/export').status_code==200
    # Completed work survives recovery without provider calls or duplicate artifacts.
    recover()
    second=ChatProviders()
    await process_chat(tid,second)
    assert not second.searches and not second.messages
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Artifact))==1
        assert db.scalar(select(func.count()).select_from(Message).where(Message.role=='assistant'))==1


@pytest.mark.asyncio
async def test_empty_discovery_preserves_queries_and_uncertainty(client):
    cid,tid=start(client)
    await process_chat(tid,ChatProviders(empty=True))
    r=artifact(client,cid)['report']
    assert all(s['search_ok'] and s['mention_status']=='not_mentioned' for s in r['scenarios'])
    assert len(report_graph(r)['nodes'])==8
    assert not report_graph(r)['links']
    assert r['blocks'][0]['kind']=='uncertainty'


@pytest.mark.asyncio
async def test_followups_are_chat_only_even_after_search(client):
    cid,tid=start(client)
    await process_chat(tid,ChatProviders())
    first=artifact(client,cid)
    question=send(client,cid,text='解释这份报告').json()['id']
    no_search=ChatProviders()
    await process_chat(question,no_search)
    assert no_search.searches==[]
    assert artifact(client,cid)['id']==first['id']
    question=send(client,cid,text='请补充最新资料').json()['id']
    await process_chat(question,ChatProviders(queries=['深圳市 拌饭 最新资料']))
    updated=artifact(client,cid)
    assert updated==first
    conversation=client.get(f'/api/v1/conversations/{cid}').json()
    answer=conversation['messages'][-1]['content']
    assert 'artifact_id' not in answer
    assert answer['blocks'] and any(p['id'].startswith(question) for p in answer['evidence'])
    assert answer['queries'][0]['query']=='深圳市 拌饭 最新资料'
    assert client.get('/api/v1/artifacts/'+first['id']).json()==first


@pytest.mark.asyncio
async def test_optional_coverage_has_no_recommendation_calls(client):
    cid,tid=start(client)
    await process_chat(tid,ChatProviders(empty=True))
    req=send(client,cid,kind='coverage',plan=plan().model_dump())
    assert req.status_code==202,req.text
    fake=ChatProviders()
    await process_chat(req.json()['id'],fake)
    a=artifact(client,cid)
    assert a['kind']=='coverage'
    assert len(a['report']['scenarios'])==24
    assert 'recommendation' not in a['report']['metrics']
    assert a['report']['metrics']['coverage']['denominator']==24
    assert not any('独立推荐' in m[0]['content'] for m in fake.messages)


@pytest.mark.asyncio
async def test_cancel_queued_and_retry(client):
    cid,tid=start(client)
    client.post('/api/v1/chat-tasks/'+tid+'/cancel')
    fake=ChatProviders()
    await process_chat(tid,fake)
    assert not fake.searches
    assert client.get('/api/v1/chat-tasks/'+tid).json()['status']=='cancelled'
    r=client.post('/api/v1/chat-tasks/'+tid+'/retry',headers={'Idempotency-Key':'retry-test'})
    assert r.status_code==202
    await process_chat(r.json()['id'],ChatProviders())
    assert artifact(client,cid)['report']['status']=='completed'


@pytest.mark.asyncio
async def test_partial_search_and_limits(client):
    class Broken(ChatProviders):
        async def search(self,query):
            self.searches.append(query)
            if len(self.searches)==2:
                raise ProviderError('测试限流，部分失败')
            return raw_page()
    cid,tid=start(client)
    await process_chat(tid,Broken())
    r=artifact(client,cid)['report']
    assert r['status']=='partial'
    assert sum(s['search_ok'] for s in r['scenarios'] if s['search_kind']!='content')==5
    with Session() as db:
        t=db.get(ChatTask,tid)
        assert t.search_calls<=12 and t.model_calls<=12


def test_invalid_citations_cannot_enter_answer():
    pages=parse_pages(raw_page(), 'fixture')
    answer=Answer.model_validate({'blocks':[{'text':'编造事实','kind':'fact','citations':[{'evidence_id':'invented','quote':'假数据'}]}]})
    assert checked_answer(answer,pages)['blocks'][0]['kind']=='uncertainty'


@pytest.mark.asyncio
async def test_foreign_evidence_and_brand_query_rejected(client):
    cid,tid=start(client)
    await process_chat(tid,ChatProviders())
    assert send(client,cid,text='分析',evidence_ids=['invented']).status_code==422
    bad=plan().model_dump();bad['scenarios'][0]['query']='深圳市 老王拌饭'
    assert send(client,cid,kind='coverage',plan=bad).status_code==422
    assert send(client,cid,kind='competitors',competitors=['虚构饭店']).status_code==422


@pytest.mark.asyncio
async def test_restart_reuses_saved_searches(client):
    cid,tid=start(client)
    fake=ChatProviders()
    await process_chat(tid,fake)
    with Session.begin() as db:
        db.get(ChatTask,tid).status='running'
    recover()
    resumed=ChatProviders()
    await process_chat(tid,resumed)
    assert not resumed.searches
    assert len(client.get(f'/api/v1/conversations/{cid}').json()['artifacts'])==1


@pytest.mark.asyncio
async def test_claim_review_rejects_unsupported_answer(client):
    class Rejecting(ChatProviders):
        async def llm(self, messages):
            if '独立审核候选回答' in messages[0]['content']:
                return {'data': {'supported_indices': []}}
            return await super().llm(messages)
    cid,tid=start(client)
    await process_chat(tid,Rejecting())
    assert all(b['kind']=='uncertainty' for b in artifact(client,cid)['report']['blocks'])


@pytest.mark.asyncio
async def test_configuration_failure_keeps_partial_evidence_and_no_calls_after_fatal(client):
    class Unauthorized(ChatProviders):
        async def search(self, query):
            self.searches.append(query)
            raise ProviderError('测试认证失败', fatal=True)
    cid,tid=start(client);fake=Unauthorized()
    await process_chat(tid,fake)
    assert len(fake.searches)==1 and not fake.messages
    r=artifact(client,cid)['report']
    assert r['status']=='partial' and r['scenarios'][0]['match']=='unavailable'
    assert r['blocks']==[]


@pytest.mark.asyncio
async def test_confirm_suggestion_changes_only_future_profile(client):
    cid,tid=start(client);await process_chat(tid,ChatProviders())
    a=artifact(client,cid);old=a['report']['profile']
    page=a['report']['scenarios'][0]['pages'][0]
    with Session.begin() as db:
        row=db.get(Artifact,a['id'])
        row.report={**row.report,'suggestions':[{'field':'address','value':'南山区学府路一号','status':'supported','reason':'测试原文地址','citations':[{'evidence_id':page['id'],'quote':page['passage']}]}]}
        db.get(Conversation,cid).plan=plan().model_dump()
    resp=client.patch(f'/api/v1/conversations/{cid}/profile',json={'artifact_id':a['id'],'indices':[0]})
    assert resp.status_code==200,resp.text
    assert resp.json()['profile']['address']=='南山区学府路一号'
    assert resp.json()['plan']=={}
    assert client.get('/api/v1/artifacts/'+a['id']).json()['report']['profile']==old


def test_graph_separates_same_brand_other_store():
    p=parse_pages(raw_page(),'same')[0]
    entities=[{'name':'老王拌饭','city':'深圳市','address':address,'is_target':True,'status':status,'reason':'门店依据','citations':[{'evidence_id':p['id'],'quote':'老王拌饭'}]} for address,status in [('一号','confirmed'),('二号','other_store')]]
    g=report_graph({'scenarios':[{'id':'q1','query':'深圳市 拌饭','pages':[p],'entities':entities}]})
    assert len([n for n in g['nodes'] if n['type']=='business'])==2


@pytest.mark.asyncio
async def test_hard_search_budget_includes_retries(client):
    from app.engine import Executor
    cid,tid=start(client)
    class Transient(ChatProviders):
        async def search(self,q):
            self.searches.append(q)
            raise ProviderError('临时错误',retryable=True)
    fake=Transient();ex=Executor(tid,ChatTask,fake,search_limit=1)
    assert await ex.search('budget','深圳市 拌饭') is None
    assert len(fake.searches)==1
    with Session() as db:
        assert db.get(ChatTask,tid).search_calls==1


@pytest.mark.asyncio
async def test_plan_edits_are_versioned_and_legacy_reports_stay_readable(client):
    cid,tid=start(client);await process_chat(tid,ChatProviders(empty=True))
    first=send(client,cid,kind='needs',text='生成需求').json()['id']
    await process_chat(first,ChatProviders())
    current=client.get(f'/api/v1/conversations/{cid}').json()['plan']
    assert current['version']==1
    second=send(client,cid,kind='needs',text='确认需求',plan=current).json()['id']
    await process_chat(second,ChatProviders())
    updated=client.get(f'/api/v1/conversations/{cid}').json()['plan']
    assert updated['version']==2
    experiment=send(client,cid,kind='experiment',plan=updated).json()['id']
    await process_chat(experiment,ChatProviders(empty=True))
    with Session() as db:
        run_id=db.get(ChatTask,experiment).run_id
    legacy=client.get('/api/v1/runs/'+run_id+'/report')
    assert legacy.status_code==200 and legacy.json()['snapshot']['plan']['version']==2
    assert client.get('/api/v1/runs/'+run_id+'/graph').status_code==200

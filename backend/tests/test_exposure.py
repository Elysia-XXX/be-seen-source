import json
import pytest
from app.exposure import discovery_queries, name_mentions, checked_content, QueryContent
from app.chat import process_chat
from app.domain import parse_pages
from app.providers import ProviderError
from .fixtures import PROFILE, raw_page
from .test_chat import ChatProviders, start, artifact, send


def test_demands_use_multiple_tags_and_do_not_depend_on_target_identity():
    profile=PROFILE.model_copy(update={'category':'中餐','tags':['朝鲜族','快餐'], 'district':'南山区','service_modes':['外卖']})
    queries=discovery_queries(profile)
    demand=[q['query'] for q in queries if q['search_kind']=='discovery']
    assert len(demand)==5
    assert all(profile.name not in q for q in demand)
    for field in ('中餐','朝鲜族','快餐','南山区','外卖'):
        assert any(field in q for q in demand)
    assert queries[-1]['search_kind']=='brand'


def test_visible_name_is_recorded_even_with_other_city_or_no_address():
    pages=parse_pages(raw_page('北京老王拌饭，具体门店未知。'),'s1')
    mentions=name_mentions(PROFILE,pages)
    assert len(mentions)==1 and mentions[0]['rank']==1
    assert mentions[0]['quote'] in pages[0]['passage']
    assert '另行核对' in mentions[0]['identity_note']


def test_content_quotes_do_not_allow_invented_merchants_or_claims():
    pages=parse_pages(raw_page('老李饭店有工作日午餐套餐。'),'s1')
    result=checked_content(QueryContent(clues=[{'evidence_id':'s1-e01','merchants':['老李饭店','虚构饭店'],
        'angles':['工作日午餐套餐','营销效果翻倍'], 'citations':[{'evidence_id':'s1-e01','quote':'老李饭店有工作日午餐套餐。'}]}]),pages)
    assert result['clues'][0]['merchants']==['老李饭店']
    assert result['clues'][0]['angles']==['工作日午餐套餐']


class UnknownTarget(ChatProviders):
    async def search(self,q):
        self.searches.append(q)
        return {'Pages':[]} if PROFILE.name in q else raw_page('老李饭店有工作日午餐套餐。',title='午餐探店')
    async def llm(self,messages):
        data=json.loads(messages[1]['content'])
        if '分析这条需求查询下' in messages[0]['content']:
            self.messages.append(messages)
            return {'data':{'clues':[{'evidence_id':e['evidence_id'],'merchants':['老李饭店'],'angles':['工作日午餐套餐'],
                'citations':[{'evidence_id':e['evidence_id'],'quote':e['passage']}]} for e in data['evidence']]}}
        return await super().llm(messages)


@pytest.mark.asyncio
async def test_unknown_target_still_produces_query_report_and_content_followup(client):
    cid,tid=start(client);fake=UnknownTarget()
    await process_chat(tid,fake)
    a=artifact(client,cid);r=a['report']
    assert r['status']=='completed'
    assert r['exposure']['successful_queries']==5
    assert r['exposure']['mentioned_queries']==0
    assert r['competitors'][0]['name']=='老李饭店'
    assert r['scenarios'][0]['content_clues'][0]['angles']==['工作日午餐套餐']
    assert all(not s['entities'] for s in r['scenarios'])
    assert not any('提取搜索证据中同城' in m[0]['content'] for m in fake.messages)
    follow=send(client,cid,kind='competitors',competitors=['老李饭店'])
    assert follow.status_code==202
    follow_fake=UnknownTarget();await process_chat(follow.json()['id'],follow_fake)
    assert len(follow_fake.searches)==3
    assert any('探店' in q for q in follow_fake.searches)
    assert artifact(client,cid)['id']==a['id']


@pytest.mark.asyncio
async def test_content_model_failure_never_blocks_demand_search_or_name_counts(client):
    class Broken(ChatProviders):
        async def llm(self,messages):
            raise ProviderError('模型暂不可用',fatal=True)
    cid,tid=start(client);fake=Broken()
    await process_chat(tid,fake)
    r=artifact(client,cid)['report']
    assert len(fake.searches)==6
    assert r['exposure']['mentioned_queries']==5
    assert r['exposure']['successful_queries']==5
    assert r['status']=='partial'


@pytest.mark.asyncio
async def test_followup_uses_selected_query_context_without_changing_base_counts(client):
    cid,tid=start(client);await process_chat(tid,UnknownTarget())
    a=artifact(client,cid);page=a['report']['scenarios'][0]['pages'][0]
    q=send(client,cid,text='追查这篇探店',artifact_id=a['id'],evidence_ids=[page['id']]).json()['id']
    fake=ChatProviders(queries=['老李饭店 探店 视频']);await process_chat(q,fake)
    decision=next(json.loads(m[1]['content']) for m in fake.messages if '判断用户当前问题' in m[0]['content'])
    assert decision['selected_evidence_ids']==[page['id']]
    assert decision['query_results'][0]['query']==a['report']['scenarios'][0]['query']
    assert artifact(client,cid)['report']['exposure']['successful_queries']==5


@pytest.mark.asyncio
async def test_saved_report_reanalysis_never_repeats_search_or_overwrites_history(client):
    cid,tid=start(client);await process_chat(tid,UnknownTarget())
    original=artifact(client,cid)
    response=client.post('/api/v1/artifacts/'+original['id']+'/reframe',headers={'Idempotency-Key':'reframe-1'})
    assert response.status_code==202
    repeated=client.post('/api/v1/artifacts/'+original['id']+'/reframe',headers={'Idempotency-Key':'reframe-1'})
    assert repeated.json()['id']==response.json()['id']
    fake=UnknownTarget();await process_chat(response.json()['id'],fake)
    assert fake.searches==[]
    revised=artifact(client,cid)
    assert revised['version']==2
    assert revised['report']['reuses_artifact_id']==original['id']
    assert revised['report']['competitors'][0]['name']=='老李饭店'
    assert client.get('/api/v1/artifacts/'+original['id']).json()==original

import json
import pytest
from app.exposure import discovery_queries, reading_hint, reading_view, article_followups
from app.domain import parse_pages
from app.chat import process_chat
from app.providers import ProviderError
from .fixtures import PROFILE, raw_page
from .test_chat import start, send, artifact, ChatProviders


def test_demand_queries_do_not_mix_in_article_search_intent():
    plan=discovery_queries(PROFILE.model_copy(update={'tags':['朝鲜族','快餐','石锅','辣味','午餐']}))
    assert len(plan)==6
    assert plan[0]['query']=='深圳市 拌饭'
    assert all(not any(w in s['query'] for w in ('探店体验','推荐理由','体验分享','门店介绍')) for s in plan[:5])
    assert all(PROFILE.name not in s['query'] for s in plan[:5])


def test_reading_order_surfaces_articles_without_rewriting_provider_ranks():
    pages=[]
    for rank,(title,url) in enumerate([
        ('广州牛肉粿条人气商户排行','https://m.dianping.com/dishes/list/test'),
        ('一顿潮汕牛肉火锅试吃','https://www.weibo.com/123/abc'),
        ('潮牛火锅','https://hk.trip.com/restaurant/china/guangzhou/detail/1'),
        ('三元里品正宗牛肉火锅,食材新鲜','http://www.xinchufang.com/shipu/123')],1):
        p=parse_pages(raw_page('公开摘要',url=url,title=title),str(rank))[0]
        p['rank']=rank;pages.append(p)
    raw={'scenarios':[{'pages':pages}]}
    result=reading_view(raw)['scenarios'][0]
    assert result['reading_ids'][:2]==[pages[1]['id'],pages[3]['id']]
    assert [p['rank'] for p in result['pages']]==[1,2,3,4]
    assert 'reading_hint' not in raw['scenarios'][0]['pages'][0]
    assert reading_hint(pages[0])['kind']=='ranking'


@pytest.mark.asyncio
async def test_rankings_trigger_bounded_article_followups(client):
    class Ranked(ChatProviders):
        async def search(self,q):
            self.searches.append(q)
            if '推荐理由 图文' in q and '探店体验' in q:
                return raw_page('深圳市老王拌饭探店分享。',title='一次拌饭探店体验',url='https://example.test/article/1')
            return raw_page(title='深圳拌饭排行榜',url='https://example.test/ranking')
    cid,tid=start(client);fake=Ranked();await process_chat(tid,fake)
    report=artifact(client,cid)['report']
    extra=[s for s in report['scenarios'] if s['search_kind']=='content']
    assert len(extra)==2 and len(fake.searches)==8
    assert all(s['parent_query'] for s in extra)
    assert report['exposure']['planned_queries']==5
    assert report['calls']['search']<=12 and report['calls']['model']<=12
    assert article_followups([{'search_kind':'content'}],{})==[]


@pytest.mark.asyncio
async def test_followup_evidence_survives_refresh_and_is_used_in_next_answer(client):
    cid,tid=start(client);await process_chat(tid,ChatProviders())
    first=artifact(client,cid)
    q=send(client,cid,text='再找一篇介绍文章').json()['id']
    await process_chat(q,ChatProviders(queries=['深圳 拌饭 探店体验']))
    dialogue=client.get(f'/api/v1/conversations/{cid}').json()
    answer=dialogue['messages'][-1]['content']
    added=next(p for p in answer['evidence'] if p['id'].startswith(q))
    q2=send(client,cid,text='解释刚找到的内容',evidence_ids=[added['id']]).json()['id']
    fake=ChatProviders();await process_chat(q2,fake)
    assert not fake.searches
    request=next(json.loads(m[1]['content']) for m in fake.messages if '判断用户当前问题' in m[0]['content'])
    assert added['id'] in [p['evidence_id'] for p in request['evidence']]
    assert any(s['query']=='深圳 拌饭 探店体验' for s in request['query_results'])
    assert artifact(client,cid)==first
    assert len(client.get(f'/api/v1/conversations/{cid}').json()['artifacts'])==1


@pytest.mark.asyncio
async def test_failed_followup_keeps_sources_without_creating_report(client):
    class FailedAnswer(ChatProviders):
        async def llm(self,messages):
            if '以正常对话直接回答' in messages[0]['content']:
                raise ProviderError('模型暂不可用',fatal=True)
            return await super().llm(messages)
    cid,tid=start(client);await process_chat(tid,ChatProviders())
    first=artifact(client,cid)
    q=send(client,cid,text='继续查资料').json()['id']
    await process_chat(q,FailedAnswer(queries=['深圳 拌饭 推荐理由']))
    dialogue=client.get(f'/api/v1/conversations/{cid}').json()
    assert len(dialogue['artifacts'])==1
    content=dialogue['messages'][-1]['content']
    assert 'artifact_id' not in content
    assert any(p['id'].startswith(q) for p in content['evidence'])
    assert content['queries']
    assert artifact(client,cid)==first

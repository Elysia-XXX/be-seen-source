"""Isolated browser-test server. Never run against the real workspace database."""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

test_dir = tempfile.TemporaryDirectory(prefix='beseen-browser-')
os.environ.update(DATABASE_URL='sqlite:///'+str(Path(test_dir.name)/'ui.db'), DEMO_PASSWORD='browser-test-access',
                  BASE_URL='https://model.example.test/v1',API_KEY='fixture',MODEL='synthetic-test-model',
                  TENCENTCLOUD_SECRET_ID='fixture',TENCENTCLOUD_SECRET_KEY='fixture')

from app.main import app
from app.db import Session, ChatTask, Artifact, init_db
from app.chat import process_chat
from sqlalchemy import select
from tests.test_chat import ChatProviders
from tests.fixtures import raw_page
from app.providers import ProviderError


class BrowserProviders(ChatProviders):
    async def read_article(self, url):
        return {'status':'read','url':url,'text':'合成测试正文：老王拌饭提供工作日午餐套餐，本文展示餐品搭配与点餐步骤。','fetched_at':'2026-10-25T00:00:00+00:00','truncated':False}
    async def llm(self, messages):
        data = json.loads(messages[1]['content'])
        if '判断用户当前问题' in messages[0]['content'] and '找一篇新的探店文章' in data.get('question', ''):
            return {'data': {'queries': ['深圳市 拌饭 探店体验 推荐理由']}}
        return await super().llm(messages)
    async def search(self, query):
        if '故障测试' in query:
            raise ProviderError('合成测试：搜索服务暂不可用', fatal=True)
        self.searches.append(query)
        if len(self.searches)%4==0:
            return {'Pages': [], 'RequestId':'synthetic-empty'}
        return raw_page(title='合成测试探店证据 · 非真实网页',url='https://example.test/synthetic/'+str(len(self.searches)))


async def worker():
    while True:
        with Session() as db:
            t=db.scalar(select(ChatTask).where(ChatTask.status=='queued'))
        if t:
            await process_chat(t.id,BrowserProviders())
            with Session.begin() as db:
                a=db.scalar(select(Artifact).where(Artifact.task_id==t.id))
                if a:
                    a.report={**a.report,'title':'合成测试 · '+a.report.get('title','报告')}
        await asyncio.sleep(.1)


@asynccontextmanager
async def lifecycle(_):
    init_db()
    task=asyncio.create_task(worker())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app.router.lifespan_context=lifecycle

import json
from app.schemas import Profile, Plan, GROUPS

PROFILE = Profile(name="老王拌饭", city="深圳市", category="拌饭", aliases=["老王饭馆"], tags=["朝鲜族"])


def plan():
    intents = ["石锅", "牛肉", "蔬菜", "鸡肉", "海鲜", "泡菜", "朝鲜族", "风味", "辣味", "清淡", "热食", "配菜", "午餐", "晚餐", "朋友聚餐", "独自就餐", "周末用餐", "工作日用餐", "城市中心", "市内选择", "不同商圈", "交通便利", "城市周边", "本地口碑"]
    return Plan(version=1, scenarios=[{"id": f"s{i+1:02}", "group": GROUPS[i // 6],
            "question": f"深圳市有哪些适合{intent}需求的拌饭选择？", "query": f"深圳市 {intent} 拌饭",
            "intent": intent, "used_fields": ["city", "category"]} for i, intent in enumerate(intents)])


def raw_page(text="深圳市老王拌饭供应拌饭，地址南山区学府路一号。", url="https://example.test/a", title="商家介绍"):
    return {"Query": "fixture", "Pages": [json.dumps({"title": title, "passage": text, "url": url}, ensure_ascii=False)], "RequestId": "test-only", "Version": "standard"}


class FakeProviders:
    """Test-only fixtures. Never enabled in the application or production provider."""
    def __init__(self, empty=False):
        self.empty = empty
        self.searches = []
        self.messages = []

    async def search(self, query):
        self.searches.append(query)
        return {"Pages": [], "RequestId": "test-empty"} if self.empty else raw_page()

    async def llm(self, messages):
        self.messages.append(messages)
        system = messages[0]["content"]
        data = json.loads(messages[1]["content"])
        if "资料补充" in system:
            result = {"suggestions": []}
        elif "恰好 24" in system:
            result = plan().model_dump()
            result["version"] = data["version"]
        elif "提取搜索证据" in system:
            evidence = data["evidence"][0]
            result = {"entities": [{"name": "老王拌饭", "status": "confirmed", "is_target": True, "city": "深圳市", "category": "拌饭", "address": "南山区学府路一号", "reason": "摘要有名称、城市和品类。", "citations": [{"evidence_id": evidence["evidence_id"], "quote": evidence["passage"]}]}]}
        elif "独立核验候选推荐" in system:
            result = {"checks": [{"name": r["name"], "supported": True, "reason": "合成测试核验通过", "citations": r["citations"]} for r in data["candidates"]]}
        elif "独立推荐" in system:
            evidence = data["evidence"][0]
            result = {"recommendations": [{"name": "老王拌饭", "reason": "摘要提供拌饭服务。", "citations": [{"evidence_id": evidence["evidence_id"], "quote": evidence["passage"]}]}]}
        else:
            result = {"advice": [], "limitations": []}
        return {"data": result, "model": "test-fixture-model", "usage": {"total_tokens": 1}}


class CompetitiveProviders(FakeProviders):
    async def search(self, query):
        self.searches.append(query)
        index = len(self.searches)
        texts = ["深圳市老李饭店提供拌饭和工作日午餐套餐。", "深圳市小陈食堂提供拌饭和蔬菜。"]
        if index % 3 == 0:
            texts.insert(0, "深圳市老王拌饭提供朝鲜族拌饭。")
        return {"Pages": [json.dumps({"title":"合成测试证据 · 非真实网页", "passage": t,
                          "url":f"https://example.test/synthetic/{i}"}, ensure_ascii=False) for i,t in enumerate(texts)], "RequestId":"synthetic-only"}

    async def llm(self, messages):
        data = json.loads(messages[1]["content"])
        system = messages[0]["content"]
        if "提取搜索证据" in system:
            self.messages.append(messages)
            entities = []
            for e in data["evidence"]:
                name = next(n for n in ("老王拌饭", "老李饭店", "小陈食堂") if n in e["passage"])
                entities.append({"name":name,"city":"深圳市","category":"拌饭","status":"confirmed","is_target":name=="老王拌饭","reason":"合成测试数据：同城品类依据。","citations":[{"evidence_id":e["evidence_id"],"quote":e["passage"]}]})
            result = {"entities":entities}
        elif "独立推荐" in system:
            self.messages.append(messages)
            recs = []
            for e in data["evidence"][:2]:
                name = next(n for n in ("老王拌饭", "老李饭店", "小陈食堂") if n in e["passage"])
                recs.append({"name":name,"reason":"合成测试：摘要包含品类。","citations":[{"evidence_id":e["evidence_id"],"quote":e["passage"]}]})
            result = {"recommendations":recs}
        elif "提出最多四条" in system:
            self.messages.append(messages)
            evidence = next(e for e in data["evidence"] if "午餐" in e["passage"])
            result = {"advice":[{"observation":"合成测试示例：同场景竞品摘要明确介绍工作日午餐。","action":"核对并完善自身公开页面中实际提供的午餐服务信息。","retest":"保留本次24个需求场景，更新公开内容后重新测评。","citations":[{"evidence_id":evidence["evidence_id"],"quote":evidence["passage"]}]}],"limitations":["本报告全部为自动化测试合成数据，不可作为真实商家结论。"]}
        else:
            return await super().llm(messages)
        return {"data":result,"model":"synthetic-test-model","usage":{}}

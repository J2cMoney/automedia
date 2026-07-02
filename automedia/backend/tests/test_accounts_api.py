"""账号矩阵 CRUD API 集成测试 - Phase 2 FLOW-6。

覆盖:
    - POST 创建 / GET 列表 / GET 详情 / PUT 更新 / DELETE 删除 全流程
    - 平台筛选
    - 健康检查端点(坏 cookie -> INVALID)
    - auth_state 不泄漏到响应
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """启动 app(触发 lifespan -> init_db),返回 TestClient。"""
    from app.main import app
    with TestClient(app) as c:
        yield c


def _create(client, platform="xhs", nickname="测试号", theme="科技"):
    r = client.post("/api/accounts", json={
        "platform": platform, "nickname": nickname, "topic_theme": theme
    })
    assert r.status_code == 201, r.text
    return r.json()


# ---------- CRUD 全流程 ----------

class TestCRUD:
    def test_create_returns_without_auth_state(self, client):
        """创建账号,响应不含 auth_state(安全)。"""
        data = _create(client, nickname="安全测试")
        assert "auth_state" not in data, "加密 cookie 绝不能返回前端"
        assert data["nickname"] == "安全测试"
        assert data["auth_status"] == "unknown"
        assert data["has_auth"] is False
        assert data["platform_label"] == "小红书"

    def test_list_and_filter(self, client):
        """列表 + 平台筛选。"""
        _create(client, platform="dy", nickname="抖音号")
        # 全量
        r = client.get("/api/accounts")
        assert r.status_code == 200
        all_items = r.json()
        assert len(all_items) >= 1
        # 平台筛选
        r = client.get("/api/accounts", params={"platform": "dy"})
        dy_items = r.json()
        assert all(i["platform"] == "dy" for i in dy_items)
        assert any(i["nickname"] == "抖音号" for i in dy_items)

    def test_get_detail_404(self, client):
        """不存在的 id 返回 404。"""
        r = client.get("/api/accounts/999999")
        assert r.status_code == 404

    def test_update_topic(self, client):
        """更新主题。"""
        acc = _create(client, nickname="更新测试", theme="原主题")
        r = client.put(f"/api/accounts/{acc['id']}", json={"topic_theme": "新主题"})
        assert r.status_code == 200
        assert r.json()["topic_theme"] == "新主题"

    def test_delete(self, client):
        """删除后查不到。"""
        acc = _create(client, nickname="删除测试")
        r = client.delete(f"/api/accounts/{acc['id']}")
        assert r.status_code == 204
        r = client.get(f"/api/accounts/{acc['id']}")
        assert r.status_code == 404

    def test_invalid_platform_rejected(self, client):
        """非法平台被枚举校验拒绝。"""
        r = client.post("/api/accounts", json={
            "platform": "weibo", "nickname": "x", "topic_theme": ""
        })
        assert r.status_code == 422


# ---------- 健康检查端点 ----------

class TestHealthCheckEndpoint:
    def test_health_check_unknown_when_no_auth(self, client):
        """未登录账号健康检查 -> UNKNOWN。"""
        acc = _create(client, nickname="未登录")
        r = client.post(f"/api/accounts/{acc['id']}/health-check")
        assert r.status_code == 200
        body = r.json()
        assert body["healthy"] is False
        assert body["auth_status"] == "unknown"

    def test_health_check_detects_corrupt_cookie(self, client):
        """直接写坏 cookie 到库,健康检查必须检出 INVALID(验收硬门槛)。

        这里用合法但解密后无关键 cookie 的密文模拟"损坏/失效"。
        """
        from app.services import crypto
        from app.db import SyncSessionLocal
        from app.models.account import Account

        acc = _create(client, nickname="坏cookie测试")
        # 直接改库:写入一个"无关键 cookie"的密文(解得开但内容无效)
        bad_cipher = crypto.encrypt_cookie([{"name": "junk", "value": "x"}])
        with SyncSessionLocal() as s:
            a = s.get(Account, acc["id"])
            a.auth_state = bad_cipher
            s.commit()

        r = client.post(f"/api/accounts/{acc['id']}/health-check")
        assert r.status_code == 200
        body = r.json()
        assert body["healthy"] is False, "坏 cookie 必须被检出"
        assert body["auth_status"] == "invalid"
        assert "重新登录" in body["message"]

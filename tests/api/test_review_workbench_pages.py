from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def client() -> TestClient:
    hasher = PasswordHasher()
    account = LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER}))
    return TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts={"user": account},
        )
    )


def test_order_plan_workbench_exposes_review_evidence_and_safe_submission_states() -> None:
    test_client = client()

    page = test_client.get("/order-plans")
    script = test_client.get("/static/js/order_plans.js")

    assert 'id="plan-unavailable" class="state unavailable"' in page.text
    assert 'id="plan-decision-evidence"' in page.text
    assert 'id="plan-decision-state"' in page.text
    assert "模拟参考价，不是委托价" in page.text
    assert "记录人工决定" in page.text
    assert "服务端受理" in page.text
    assert "不可用原因" in script.text
    assert "恢复路径" in script.text
    assert "Idempotency-Key" in script.text
    assert "确认提交" in page.text
    assert "请勿重复提交" in script.text
    assert "permission" in script.text


def test_strategy_workbench_exposes_draft_review_publish_boundaries_and_submission_states() -> None:
    test_client = client()

    page = test_client.get("/strategies")
    script = test_client.get("/static/js/strategy_versions.js")

    assert 'id="strategy-unavailable" class="state unavailable"' in page.text
    assert 'id="strategy-permission" class="state permission"' in page.text
    assert 'id="strategy-review-evidence"' in page.text
    assert 'id="strategy-review-result"' in page.text
    assert "草稿、审核与发布均由服务端状态机" in page.text
    assert "不自动下单" in page.text
    assert "Idempotency-Key" in script.text
    assert "正在提交，不能重复提交" in script.text
    assert "服务端已受理" in script.text
    assert "permission" in script.text
    assert page.text.count('class="primary"') == 2

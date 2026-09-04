"""Read-only Jinja2 pages for the local research workflow."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.templating import Jinja2Templates

from app.core.dependencies import get_authenticator
from app.core.security import AuthenticationError, LocalAuthenticator, Principal, Role

_TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_ROOT))
router = APIRouter(tags=["pages"])
_PAGE_ACCESS_COOKIE = "research_page_access"
AuthenticatorDependency = Annotated[LocalAuthenticator, Depends(get_authenticator)]


def require_page_admin(
    authenticator: AuthenticatorDependency,
    research_page_access: Annotated[str | None, Cookie()] = None,
) -> Principal:
    if not research_page_access:
        raise AuthenticationError("page authentication is required")
    principal = authenticator.authenticate_access_token(research_page_access)
    authenticator.require_roles(principal, Role.ADMIN)
    return principal


PageAdmin = Annotated[Principal, Depends(require_page_admin)]


def _render(request: Request, template: str, **context: Any) -> Any:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={"data_date": date.today().isoformat(), "risk_state": "未指定", **context},
    )


@router.get("/")
def dashboard(request: Request) -> Any:
    return _render(
        request,
        "dashboard.html",
        title="运行总览",
        batches_api_url="/api/v1/data/batches",
        backtests_api_url="/api/v1/backtests",
        bars_api_url="/api/v1/data/bars",
    )


@router.get("/login")
def login_page(request: Request) -> Any:
    return _render(request, "login.html", title="登录")


@router.get("/dashboard")
def dashboard_page(request: Request) -> Any:
    return dashboard(request)


@router.get("/data-quality")
def data_quality_page(request: Request) -> Any:
    batch_id = request.query_params.get("batch_id")
    quality_api_url = (
        f"/api/v1/data/batches/{batch_id}/quality" if batch_id else "/api/v1/data/batches"
    )
    return _render(request, "data_quality.html", title="数据质量", quality_api_url=quality_api_url)


@router.get("/data-import")
def data_import_page(request: Request, _: PageAdmin) -> Any:
    return _render(request, "data_import.html", title="导入授权行情")


@router.get("/universe")
def universe_page(request: Request) -> Any:
    trade_date = request.query_params.get("trade_date") or date.today().isoformat()
    return _render(
        request,
        "universe.html",
        title="历史股票池",
        trade_date=trade_date,
        pool_api_url=f"/api/v1/securities/pool?trade_date={trade_date}",
        bars_api_url=f"/api/v1/data/bars?trade_date={trade_date}",
    )


@router.get("/strategies")
def strategies_page(request: Request) -> Any:
    return _render(request, "strategy_versions.html", title="策略版本")


@router.get("/backtests")
def backtests_page(request: Request) -> Any:
    return _render(
        request,
        "backtest_detail.html",
        title="回测运行",
        backtest_api_url="/api/v1/backtests",
    )


@router.get("/backtests/create")
def backtest_create_page(request: Request) -> Any:
    return _render(request, "backtest_create.html", title="新建回测")


@router.get("/backtests/{run_id}/view")
def backtest_detail_page(run_id: str, request: Request) -> Any:
    return _render(
        request,
        "backtest_detail.html",
        title="回测运行详情",
        run_id=run_id,
        backtest_api_url=f"/api/v1/backtests/{run_id}",
    )


@router.get("/daily-reports/{report_date}")
def daily_report_page(report_date: date, request: Request) -> Any:
    return _render(
        request,
        "daily_report.html",
        title="日报",
        report_date=report_date.isoformat(),
    )


@router.get("/daily-flow")
def daily_flow_page(request: Request, _: PageAdmin) -> Any:
    return _render(request, "daily_flow.html", title="执行日终")


@router.get("/order-plans")
def order_plans_page(request: Request) -> Any:
    return _render(request, "order_plans.html", title="人工计划确认")


@router.get("/executions")
def executions_page(request: Request) -> Any:
    return _render(request, "executions.html", title="人工成交录入")


@router.get("/reports")
def reports_page(request: Request) -> Any:
    return _render(request, "reports.html", title="报告与偏差")


@router.get("/admin")
def admin_page(request: Request) -> Any:
    return _render(request, "admin.html", title="管理与审计")


__all__ = ["router"]

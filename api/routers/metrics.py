"""可观测性路由：LLM 成本面板。"""
from fastapi import APIRouter, Depends

from api import deps
from tools.observability import usage_snapshot

router = APIRouter(tags=["运维"])


@router.get("/metrics/cost")
async def cost_metrics(current_user: dict = Depends(deps.get_current_user)):
    """LLM 成本面板：进程内累计的调用次数与 token 用量

    只统计当前 worker 进程，多 worker 部署时需要在网关侧汇总；
    单价由 COST_INPUT_PER_1K / COST_OUTPUT_PER_1K 配置，未配置时不估算金额。
    """
    return {"success": True, "usage": usage_snapshot()}

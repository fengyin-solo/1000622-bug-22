"""检测任务接口：维护检测任务，覆盖派发任务、提交复核、确认完成、驳回重派等动作。

状态流转判定全部下沉到 TaskService，接口层只做参数接收与结果包装。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.task import PRIORITIES, TaskService

router = APIRouter(prefix="/api/task", tags=["检测任务"])

service = TaskService()

LIST_FIELDS = ["任务编号", "关联样品", "检测项目", "承检人员", "计划完成日", "实际完成日", "任务优先级", "任务状态"]
STATUSES = ["待派发", "检测中", "待复核", "已完成"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待派发、检测中、待复核、已完成"),
    assignee: str | None = Query(default=None, description="按承检人员精确筛选"),
    priority: str | None = Query(default=None, description="按任务优先级筛选：高、中、低"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号、状态、承检人员与优先级过滤检测任务列表；结果按优先级与计划完成日排序。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"任务状态只能是：{'、'.join(STATUSES)}")
    if priority and priority not in PRIORITIES:
        raise HTTPException(status_code=400, detail=f"任务优先级只能是：{'、'.join(PRIORITIES)}")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        assignee=assignee,
        priority=priority,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def workload_stats() -> dict[str, Any]:
    """任务看板统计：各状态任务数、超期数、每位承检人员的待派发/检测中在手量。"""
    return service.workload()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出检测任务清单：返回当前过滤条件下的全量数据（同样按优先级排序）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "task", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条检测任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检测任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条检测任务，缺字段或任务编号重复时说明原因而不是静默丢弃。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="检测任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检测任务执行派发任务、提交复核、确认完成、驳回重派；越级或重复动作会被拦下。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """调整检测任务：更换承检人员、任务优先级与计划完成日；换人后双方在手量按列表实时重算。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)

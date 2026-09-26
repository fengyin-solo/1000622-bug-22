"""检测任务业务规则：状态流转、字段校验、筛选排序与人员在手量都收在这里。

流转口径（全系统唯一一份，接口层不再自行判定）：
    待派发 --派发任务--> 检测中 --提交复核--> 待复核 --确认完成--> 已完成
    待复核 --驳回重派--> 待派发（重新派发仍走「派发任务」，故重复派发只生效一次）

关键约定：
- 每个动作只允许在指定的源状态执行，重复派发、检测中直接完成等越级流转一律拦下。
- 承检人员换人走 update_entry；列表与人员统计每次实时汇总，换人后双方的
  待派发/检测中数量随之变化，不依赖任何缓存计数。
- 超期标记按「计划完成日 < 今天 且 尚未完成」实时计算，完成的任务不再带超期。
- 列表固定按任务优先级（高/中/低）、计划完成日、任务编号排序，改优先级后顺序即时生效。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "关联样品", "检测项目"]
EDITABLE_FIELDS = ["承检人员", "计划完成日", "实际完成日", "任务优先级"]
PRIORITIES = ["高", "中", "低"]
STATUS_ORDER = ["待派发", "检测中", "待复核", "已完成"]
FINAL_STATUS = "已完成"

# 动作 -> 允许的源状态 / 目标状态
ACTION_RULES: dict[str, dict[str, str]] = {
    "派发任务": {"from": "待派发", "to": "检测中"},
    "提交复核": {"from": "检测中", "to": "待复核"},
    "确认完成": {"from": "待复核", "to": "已完成"},
    "驳回重派": {"from": "待复核", "to": "待派发"},
}
# 既有动作入口保持原名，驳回是新增入口，其它模块与复核记录不受影响。


def _priority_rank(value: Any) -> int:
    try:
        return PRIORITIES.index(str(value).strip())
    except ValueError:
        return PRIORITIES.index("中")


def _is_overdue(row: dict[str, Any], *, today: str | None = None) -> bool:
    """未完成且计划完成日早于今天才算超期；已完成或日期缺失不算。"""
    if row.get("status") == FINAL_STATUS:
        return False
    planned = str(row.get("计划完成日") or "").strip()
    if not planned:
        return False
    today_str = today or date.today().isoformat()
    return planned < today_str


def _sync_row(row: dict[str, Any], *, today: str | None = None) -> dict[str, Any]:
    """把派生字段统一回写到行上：显示用的任务状态、pending、超期异常标记。

    每次读取都重算一遍，保证刷新或从复核页返回后看到的状态/超期标记与数据一致。
    """
    status = str(row.get("status") or STATUS_ORDER[0])
    row["status"] = status
    row["任务状态"] = status
    row["pending"] = status != FINAL_STATUS
    overdue = _is_overdue(row, today=today)
    row["超期"] = overdue
    row["abnormal"] = overdue
    return row


class TaskService:
    # ---------- 查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        priority: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        today = date.today().isoformat()
        rows = [_sync_row(dict(row), today=today) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if assignee:
            rows = [row for row in rows if str(row.get("承检人员") or "").strip() == assignee]
        if priority:
            rows = [row for row in rows if str(row.get("任务优先级") or "").strip() == priority]
        # 统一排序口径：优先级高->低，计划完成日近->远，任务编号兜底
        rows.sort(key=lambda row: (
            _priority_rank(row.get("任务优先级")),
            str(row.get("计划完成日") or "9999-12-31"),
            str(row.get("任务编号") or ""),
        ))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return _sync_row(entry) if entry is not None else None

    # ---------- 登记 ----------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        rows = store.rows(MODULE)
        code = str(values.get("任务编号")).strip()
        if any(str(row.get("任务编号") or "").strip() == code for row in rows):
            return None, f"任务编号 {code} 已存在，不能重复登记派发"
        priority = str(values.get("任务优先级") or "中").strip()
        if priority not in PRIORITIES:
            return None, f"任务优先级只能是：{'、'.join(PRIORITIES)}"
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 派发前可以先指定承检人员；未指定时留空，派发/换人接口再补。
        entry["承检人员"] = str(values.get("承检人员") or "").strip()
        entry["计划完成日"] = str(values.get("计划完成日") or "").strip()
        entry["实际完成日"] = ""
        entry["任务优先级"] = priority
        entry["status"] = STATUS_ORDER[0]
        rows.append(entry)
        return _sync_row(entry), ""

    # ---------- 状态流转 ----------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测任务可执行范围"
        rule = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current != rule["from"]:
            return None, self._block_message(action, current)
        target = rule["to"]
        if action == "派发任务" and not str(entry.get("承检人员") or "").strip():
            return None, "派发前请先指定承检人员"
        entry["status"] = target
        if target == FINAL_STATUS:
            entry["实际完成日"] = str(entry.get("实际完成日") or "").strip() or date.today().isoformat()
        if action == "驳回重派":
            # 驳回后回到可重新派发的状态，检测结果需重做，实际完成日清空
            entry["实际完成日"] = ""
        return _sync_row(entry), f"检测任务已{action}"

    @staticmethod
    def _block_message(action: str, current: str) -> str:
        if action == "派发任务":
            return f"任务当前为「{current}」，只有待派发任务可派发，重复派发不会再次生效"
        if action == "提交复核":
            return f"任务当前为「{current}」，只有检测中的任务可提交复核"
        if action == "确认完成":
            return f"任务当前为「{current}」，只有待复核的任务可确认完成，不能越级跳转"
        if action == "驳回重派":
            return f"任务当前为「{current}」，只有待复核的任务可驳回重派"
        return f"任务当前为「{current}」，不能执行{action}"

    # ---------- 换人 / 调整 ----------
    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        unknown = [field for field in values if field not in EDITABLE_FIELDS]
        if unknown:
            return None, f"字段「{'、'.join(unknown)}」不允许在任务调整中修改"
        if "任务优先级" in values:
            priority = str(values.get("任务优先级") or "").strip()
            if priority not in PRIORITIES:
                return None, f"任务优先级只能是：{'、'.join(PRIORITIES)}"
            entry["任务优先级"] = priority
        # 换人只允许发生在任务尚未完成时；完成后的任务改人不再影响在手量口径。
        if "承检人员" in values:
            if entry.get("status") == FINAL_STATUS:
                return None, "任务已完成，不能再更换承检人员"
            assignee = str(values.get("承检人员") or "").strip()
            if not assignee:
                return None, "承检人员不能为空"
            entry["承检人员"] = assignee
        for field in ("计划完成日", "实际完成日"):
            if field in values:
                entry[field] = str(values.get(field) or "").strip()
        return _sync_row(entry), "检测任务已调整"

    # ---------- 人员在手量 / 看板统计 ----------
    def workload(self) -> dict[str, Any]:
        today = date.today().isoformat()
        rows = [_sync_row(dict(row), today=today) for row in store.rows(MODULE)]
        status_counts = {status: 0 for status in STATUS_ORDER}
        overdue = 0
        assignees: dict[str, dict[str, int]] = {}
        for row in rows:
            status = str(row.get("status"))
            status_counts[status] = status_counts.get(status, 0) + 1
            if row.get("超期"):
                overdue += 1
            name = str(row.get("承检人员") or "").strip()
            if not name:
                continue
            bucket = assignees.setdefault(name, {"待派发": 0, "检测中": 0, "在手合计": 0})
            if status in ("待派发", "检测中"):
                bucket[status] += 1
                bucket["在手合计"] += 1
        people = [
            {"承检人员": name, "待派发": bucket["待派发"], "检测中": bucket["检测中"], "在手合计": bucket["在手合计"]}
            for name, bucket in assignees.items()
            if bucket["在手合计"] > 0
        ]
        people.sort(key=lambda item: (-item["在手合计"], item["承检人员"]))
        return {
            "status_counts": status_counts,
            "待派发": status_counts.get("待派发", 0),
            "检测中": status_counts.get("检测中", 0),
            "待复核": status_counts.get("待复核", 0),
            "已完成": status_counts.get("已完成", 0),
            "超期": overdue,
            "assignees": people,
        }

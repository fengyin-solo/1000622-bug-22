"""检测任务业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "关联样品", "检测项目"]
STATUS_ORDER = ["待派发", "检测中", "待复核", "已完成"]
# 每个动作只允许从指定前置状态发起：重复派发、跨级跳转、重复提交复核都会被拦下，
# 被驳回的任务回到「待派发」，可以重新走派发。
ACTION_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "派发任务": (("待派发",), "检测中"),
    "提交复核": (("检测中",), "待复核"),
    "确认完成": (("待复核",), "已完成"),
    "驳回任务": (("待复核",), "待派发"),
}
# 列表展示允许直接编辑的字段；任务状态只能走动作流转，不开放直改。
EDITABLE_FIELDS = ["任务编号", "关联样品", "检测项目", "承检人员", "计划完成日", "实际完成日", "任务优先级"]
PRIORITY_RANK = {"特急": 0, "紧急": 0, "高": 1, "中": 2, "低": 3}
COUNTED_STATUSES = ("待派发", "检测中")


class TaskService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = sorted(store.rows(MODULE), key=self._sort_key)
        for row in rows:
            self._refresh_overdue(row)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self._refresh_overdue(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        code = str(values.get("任务编号") or "").strip()
        if self._find_by_code(code) is not None:
            return None, f"任务编号「{code}」已派发过，重复登记不生效"
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in EDITABLE_FIELDS:
            if field not in entry and values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        self._refresh_overdue(entry)
        return entry, None

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        """改派承检人员、调整优先级等：只动白名单字段，状态仍走动作流转。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        updates = {field: values[field] for field in EDITABLE_FIELDS if values.get(field) is not None}
        if not updates:
            return None, f"没有可更新的字段，仅支持：{'、'.join(EDITABLE_FIELDS)}"
        if "任务编号" in updates:
            code = str(updates["任务编号"]).strip()
            if not code:
                return None, "任务编号不能为空"
            existing = self._find_by_code(code)
            if existing is not None and existing is not entry:
                return None, f"任务编号「{code}」已存在，不能重复登记"
            updates["任务编号"] = code
        entry.update(updates)
        self._refresh_overdue(entry)
        return entry, None

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测任务可执行范围"
        allowed_from, target = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current not in allowed_from:
            expect = "、".join(allowed_from)
            return None, f"检测任务当前状态为「{current}」，「{action}」只对「{expect}」状态生效，本次操作未改变任务"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        self._refresh_overdue(entry)
        return entry, f"检测任务已{action}"

    def stats(self) -> dict[str, int]:
        """列表页统计卡：待派发、检测中、超期（已完成的一律不算超期）。"""
        rows = store.rows(MODULE)
        for row in rows:
            self._refresh_overdue(row)
        return {
            "待派发任务": sum(1 for row in rows if row.get("status") == "待派发"),
            "检测中任务": sum(1 for row in rows if row.get("status") == "检测中"),
            "超期任务": sum(1 for row in rows if row.get("abnormal")),
        }

    def workload(self) -> list[dict[str, Any]]:
        """按承检人员汇总在手任务：直接从任务行推导，换人后新旧双方同时正确。"""
        counters: dict[str, dict[str, Any]] = {}
        for row in store.rows(MODULE):
            status = row.get("status")
            if status not in COUNTED_STATUSES:
                continue
            owner = str(row.get("承检人员") or "").strip() or "未指派"
            bucket = counters.setdefault(owner, {"承检人员": owner, "待派发": 0, "检测中": 0, "在手合计": 0})
            bucket[status] += 1
            bucket["在手合计"] += 1
        return sorted(counters.values(), key=lambda item: (-int(item["在手合计"]), str(item["承检人员"])))

    def _find_by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("任务编号") or "").strip() == code:
                return row
        return None

    def _sort_key(self, row: dict[str, Any]) -> tuple[int, int]:
        rank = PRIORITY_RANK.get(str(row.get("任务优先级") or "").strip(), len(PRIORITY_RANK))
        return rank, int(row.get("id", 0))

    def _refresh_overdue(self, entry: dict[str, Any]) -> None:
        """超期标记：已完成的任务一律摘掉；未完成且计划完成日已过的补挂。"""
        if entry.get("status") == STATUS_ORDER[-1]:
            entry["abnormal"] = False
            return
        plan = self._parse_date(entry.get("计划完成日"))
        if plan is not None and plan < date.today():
            entry["abnormal"] = True

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        try:
            return date.fromisoformat(str(value or "").strip())
        except ValueError:
            return None

<template>
  <section class="page" data-module="task">
    <header class="page-head">
      <div>
        <h2>检测任务管理</h2>
        <p class="page-desc">维护检测任务，围绕任务编号、关联样品、检测项目、承检人员做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记检测任务</button>
        <button class="btn" type="button" @click="exportRows">导出检测任务清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="workload.length" class="workload-row">
      <span class="workload-title">承检人员在手任务</span>
      <span v-for="item in workload" :key="item.承检人员" class="workload-item">
        {{ item.承检人员 }}：待派发 {{ item.待派发 }} · 检测中 {{ item.检测中 }}
      </span>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <form v-if="editingId !== null" class="filter-bar edit-bar" @submit.prevent="saveEdit">
      <span class="edit-title">调整任务 #{{ editingId }}（状态请走动作流转）</span>
      <label class="filter-item">
        <span>承检人员</span>
        <input v-model="editForm.承检人员" placeholder="填写新的承检人员" />
      </label>
      <label class="filter-item">
        <span>任务优先级</span>
        <select v-model="editForm.任务优先级">
          <option v-for="option in priorityOptions" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>计划完成日</span>
        <input v-model="editForm.计划完成日" placeholder="YYYY-MM-DD" />
      </label>
      <button class="btn primary" type="submit">保存调整</button>
      <button class="btn ghost" type="button" @click="cancelEdit">取消</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openEdit(row)">调整任务</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无检测任务数据，可先登记检测任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条检测任务记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type WorkloadItem = { 承检人员: string; 待派发: number; 检测中: number; 在手合计: number }

const ENDPOINT = '/api/task'
const columns = ["任务编号", "关联样品", "检测项目", "承检人员", "计划完成日", "实际完成日", "任务优先级", "任务状态"]
const actions = ["派发任务", "提交复核", "确认完成", "驳回任务"]
const statuses = ["待派发", "检测中", "待复核", "已完成"]
const priorityOptions = ["特急", "高", "中", "低"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const stats = ref([
  { label: '待派发任务', value: 0 },
  { label: '检测中任务', value: 0 },
  { label: '超期任务', value: 0 },
])
const workload = ref<WorkloadItem[]>([])
const editingId = ref<number | null>(null)
const editForm = ref({ 承检人员: '', 任务优先级: '中', 计划完成日: '' })

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '检测任务登记入口尚未接入审批流'
}

function openEdit(row: Row) {
  editingId.value = Number(row.id)
  editForm.value = {
    承检人员: String(row.承检人员 ?? ''),
    任务优先级: priorityOptions.includes(String(row.任务优先级)) ? String(row.任务优先级) : '中',
    计划完成日: String(row.计划完成日 ?? ''),
  }
  errorMessage.value = ''
}

function cancelEdit() {
  editingId.value = null
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '检测任务动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务操作失败'
  }
}

async function saveEdit() {
  if (editingId.value === null) {
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${editingId.value}`, {
      method: 'PUT',
      body: JSON.stringify({ values: { ...editForm.value } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '检测任务调整未生效，请稍后重试')
    }
    editingId.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务调整失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('检测任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await Promise.all([loadStats(), loadWorkload()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务列表读取失败'
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    stats.value = stats.value.map((item) => ({ ...item, value: Number(payload[item.label] ?? 0) }))
  } catch {
    // 统计卡读取失败时保留上一次数值，不打断列表使用
  }
}

async function loadWorkload() {
  try {
    const response = await request(`${ENDPOINT}/workload`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    workload.value = payload.items ?? []
  } catch {
    // 在手任务读取失败时保留上一次数值，不打断列表使用
  }
}

onMounted(reload)
</script>

<style scoped>
.workload-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--muted);
}
.workload-title {
  font-weight: 600;
}
.workload-item {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 10px;
}
.edit-bar {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.edit-title {
  font-size: 12px;
  color: var(--muted);
  align-self: center;
}
</style>

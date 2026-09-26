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
      <article v-for="item in cards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="workload.length" class="workload-bar">
      <span class="workload-title">承检人员在手量</span>
      <span v-for="person in workload" :key="person.承检人员" class="workload-chip">
        {{ person.承检人员 }}：待派发 {{ person.待派发 }} / 检测中 {{ person.检测中 }}
      </span>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>任务状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>任务优先级</span>
        <select v-model="filters.priority">
          <option value="">全部优先级</option>
          <option v-for="priority in priorities" :key="priority" :value="priority">{{ priority }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
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
          <td v-for="column in columns" :key="column">
            <template v-if="column === '任务状态'">
              <RouterLink v-if="row.status === '待复核'" class="review-link" :to="{ path: '/review' }">
                {{ row[column] ?? '—' }}
              </RouterLink>
              <template v-else>{{ row[column] ?? '—' }}</template>
              <span v-if="row.超期" class="overdue-tag">超期</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="!canRun(action, row)"
              :title="canRun(action, row) ? '' : actionHint(action, row)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="row.status === '待复核'"
              class="link"
              type="button"
              @click="runAction('驳回重派', row)"
            >
              驳回重派
            </button>
            <button class="link" type="button" @click="openAdjust(row)">调整</button>
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

    <div v-if="adjust.open" class="modal-mask" @click.self="closeAdjust">
      <div class="modal-card">
        <h3>调整检测任务 {{ adjust.code }}</h3>
        <label class="modal-item">
          <span>承检人员</span>
          <input v-model="adjust.form.承检人员" placeholder="换人后双方在手量自动重算" />
        </label>
        <label class="modal-item">
          <span>任务优先级</span>
          <select v-model="adjust.form.任务优先级">
            <option v-for="priority in priorities" :key="priority" :value="priority">{{ priority }}</option>
          </select>
        </label>
        <label class="modal-item">
          <span>计划完成日</span>
          <input v-model="adjust.form.计划完成日" type="date" />
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeAdjust">取消</button>
          <button class="btn primary" type="button" :disabled="adjust.saving" @click="saveAdjust">保存调整</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type WorkloadPerson = { 承检人员: string; 待派发: number; 检测中: number; 在手合计: number }

const ENDPOINT = '/api/task'
const FILTER_STORAGE_KEY = 'task.filters.v1'
const columns = ["任务编号", "关联样品", "检测项目", "承检人员", "计划完成日", "实际完成日", "任务优先级", "任务状态"]
const actions = ["派发任务", "提交复核", "确认完成"]
const statuses = ["待派发", "检测中", "待复核", "已完成"]
const priorities = ["高", "中", "低"]
// 动作 -> 当前允许执行的源状态，与后端 TaskService.ACTION_RULES 保持一致
const ACTION_FROM: Record<string, string> = {
  派发任务: "待派发",
  提交复核: "检测中",
  确认完成: "待复核",
}

const route = useRoute()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const statsData = ref<Record<string, number>>({})
const workload = ref<WorkloadPerson[]>([])
const filters = ref<Record<string, string>>(loadFilters())
const filterFields = columns.slice(0, 3)

const cards = computed(() => [
  { label: "待派发任务", value: statsData.value.待派发 ?? 0 },
  { label: "检测中任务", value: statsData.value.检测中 ?? 0 },
  { label: "超期任务", value: statsData.value.超期 ?? 0 },
])

const adjust = reactive({
  open: false,
  saving: false,
  id: 0,
  code: '',
  form: { 承检人员: '', 任务优先级: '中', 计划完成日: '' },
})

function loadFilters(): Record<string, string> {
  try {
    const saved = sessionStorage.getItem(FILTER_STORAGE_KEY)
    return saved ? (JSON.parse(saved) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function persistFilters() {
  try {
    sessionStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters.value))
  } catch {
    // 隐私模式等场景下写不进缓存时忽略，筛选条件仍在当前页有效
  }
}

function resetFilters() {
  filters.value = {}
  persistFilters()
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '检测任务登记入口尚未接入审批流'
}

function canRun(action: string, row: Row) {
  return row.status === ACTION_FROM[action]
}

function actionHint(action: string, row: Row) {
  return `当前状态为「${row.status ?? '未知'}」，不能执行${action}`
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  if (!canRun(action, row)) {
    errorMessage.value = actionHint(action, row)
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '检测任务动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务操作失败'
  }
}

function openAdjust(row: Row) {
  adjust.open = true
  adjust.id = Number(row.id)
  adjust.code = String(row.任务编号 ?? '')
  adjust.form = {
    承检人员: String(row.承检人员 ?? ''),
    任务优先级: priorities.includes(String(row.任务优先级)) ? String(row.任务优先级) : '中',
    计划完成日: String(row.计划完成日 ?? ''),
  }
}

function closeAdjust() {
  adjust.open = false
}

async function saveAdjust() {
  errorMessage.value = ''
  adjust.saving = true
  try {
    const response = await request(`${ENDPOINT}/${adjust.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ values: { ...adjust.form } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '检测任务调整未生效，请稍后重试')
    }
    closeAdjust()
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务调整失败'
  } finally {
    adjust.saving = false
  }
}

function buildQuery() {
  // 前三个输入框分别对应任务编号/关联样品/检测项目，后端只按任务编号过滤，
  // 其余键保留在缓存里但不发送，避免发无效参数。
  const mapped: Record<string, string> = {}
  if (filters.value.任务编号) mapped.keyword = filters.value.任务编号
  if (filters.value.status) mapped.status = filters.value.status
  if (filters.value.priority) mapped.priority = filters.value.priority
  return new URLSearchParams(mapped).toString()
}

async function reload() {
  errorMessage.value = ''
  persistFilters()
  try {
    const response = await request(`${ENDPOINT}?${buildQuery()}`)
    if (!response.ok) {
      throw new Error('检测任务列表读取失败')
    }
    const payload = await response.json()
    // 服务端返回的就是持久化后的状态与优先级，刷新或从复核页返回都以它为准
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
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
    statsData.value = payload
    workload.value = payload.assignees ?? []
  } catch {
    // 看板统计不阻塞列表
  }
}

onMounted(() => {
  // 从复核页带回的状态筛选（如有）与本地缓存合并，路由参数优先
  if (typeof route.query.status === 'string' && statuses.includes(route.query.status)) {
    filters.value.status = route.query.status
    persistFilters()
  }
  void Promise.all([reload(), loadStats()])
})
</script>

<style scoped>
.overdue-tag {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  background: #fef3f2;
  color: #b42318;
  border: 1px solid #fda29b;
  font-size: 12px;
}

.workload-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
}

.workload-title {
  color: var(--muted);
}

.workload-chip {
  padding: 2px 10px;
  background: #eff4ff;
  border: 1px solid #b2ccff;
  border-radius: 999px;
  color: #1d4ed8;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}

.modal-card {
  width: 360px;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}

.modal-card h3 {
  margin: 0 0 14px;
  font-size: 15px;
}

.modal-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 13px;
}

.modal-item input,
.modal-item select {
  flex: 1;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 6px;
}

.link:disabled {
  color: #9aa6b2;
  cursor: not-allowed;
}

.review-link {
  color: var(--brand);
  text-decoration: none;
}

.review-link:hover {
  text-decoration: underline;
}
</style>

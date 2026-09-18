<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Plus, ShieldCheck, TerminalSquare, Command } from 'lucide-vue-next'

type Evidence = {
  id: string; source: string; occurred_at: string
  query: { starts_at: string; ends_at: string; fields: Record<string, string> }
  snippet: { event: string; error_code: string; fields: Record<string, string> }
  locator: string | null; redaction_note: string
  source_attempt_id: string; source_attempt_number: number; verification: string
}
type Conclusion = {
  id: string; version: number; created_at: string; kind: 'hypothesis' | 'confirmed' | 'undetermined'
  verification: string; mechanism: string | null; evidence_ids: string[]; missing: string[]; boundary: string
  phenomenon?: string; causal_chain?: string; alternatives?: string; contradictions?: string
}
type QuerySource = { source: string; state: string; attempts: number; reason: string | null; retry_after_seconds?: number | null; failures: { reason: string; attempt: number; wait_seconds: number | null }[] }
type QueryRun = { id: string; status: string; sources: QuerySource[] }
const queryStates: Record<string, string> = { canceled: '已停止，结果不纳入', pending: '尚未查询', querying: '正在查询', waiting: '受限等待', succeeded: '查询成功', unavailable: '来源缺失', skipped: '跳过查询（来源缺失）' }
const queryReasons: Record<string, string> = { network: '临时网络故障', timeout: '单次查询30秒超时', permission: '权限不足', credentials: '凭据无效', parameters: '查询参数无效', rate_limited: '来源限流', inflight: '来源已有在途查询，等待退出后再试', not_configured: '未配置受控查询适配器', adapter_error: '适配器能力或返回不符合受控契约' }
const conclusionLabels = { hypothesis: '待验证假设', confirmed: '已确认根因', undetermined: '未确定根因' }
const missingLabels: Record<string, string> = { causal_trace: '同调用因果传播轨迹', dependency_observation: '依赖调用观测', alternative_exclusion: '替代解释排除依据' }
function locateEvidence(attemptId: string, id: string) {
  document.getElementById(`evidence-${attemptId}-${id}`)?.scrollIntoView({ block: 'center' })
}
function locateAttempt(id: string) {
  document.getElementById(`attempt-${id}`)?.scrollIntoView({ block: 'start' })
}
type Investigation = {
  id: string
  title: string
  description: string
  service: string | null
  status: '待处理' | '调查中' | '已完成' | '失败' | '已取消'
  severity: '未评估' | '高' | '中' | '低'
  severity_source: 'human' | 'agent' | null
  severity_assessed_at: string | null
  evidence_count: number
  attempts: { id: string; attempt_number: number; execution_phase: string; cancel_requested_at: string | null; deadline_at: string; timeout_requested_at: string | null; stop_confirmed_at: string | null; service_id: string; starts_at: string; ends_at: string; status: string; execution_mode: 'controlled'; evidence_count: number; evidence: Evidence[]; historical_reference_states: Record<string, { included_evidence_id: string | null; reference_allowed: boolean }>; evidence_notices: string[]; ended_at: string | null; result: string | null; end_reason: string | null; conclusion: Conclusion | null; conclusions: Conclusion[]; queries: QueryRun[]; query_coverage: { complete: boolean; missing_sources: string[]; notice: string } }[]
  created_at: string
  updated_at: string
  retention_expires_at: string | null
}
const services = ref<{ id: string; name: string }[]>([])
const serviceId = ref('')
const rangeMode = ref('recent')
const startsAt = ref('')
const endsAt = ref('')
const canceling = ref<string | null>(null)
const cancelError = ref('')
async function cancelInvestigation(attemptId: string) {
  if (!selected.value || canceling.value === attemptId) return
  const id = selected.value.id
  const sequence = readSequence
  const route = location.hash
  const isCurrent = () => sequence === readSequence && location.hash === route &&
    selected.value?.id === id && selected.value.attempts.some(attempt => attempt.id === attemptId) &&
    canceling.value === attemptId
  canceling.value = attemptId
  cancelError.value = ''
  try {
    const record = await request<Investigation>(`/${id}/attempts/${attemptId}/cancel`, { method: 'POST' })
    if (isCurrent()) selected.value = record
  } catch (cause) {
    if (isCurrent()) cancelError.value = cause instanceof Error ? cause.message : '取消结果未确认，请刷新状态'
  } finally {
    if (isCurrent()) canceling.value = null
  }
}
const referencing = ref<string | null>(null)
const referenceError = ref('')
function historicalState(id: string) {
  return referenceTarget()?.historical_reference_states[id]
}
function referenceTarget() {
  const latest = selected.value?.attempts.at(-1)
  return latest?.status === '调查中' && latest.execution_phase === 'running' ? latest : null
}
async function includeHistorical(evidenceId: string) {
  const target = referenceTarget()
  if (!selected.value || !target || referencing.value) return
  const id = selected.value.id
  const sequence = readSequence
  const route = location.hash
  const isCurrent = () => sequence === readSequence && location.hash === route && selected.value?.id === id
  referencing.value = evidenceId
  referenceError.value = ''
  try {
    await request(`/${id}/attempts/${target.id}/evidence-references`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evidence_id: evidenceId }),
    })
    const record = await request<Investigation>(`/${id}`)
    if (isCurrent()) selected.value = record
  } catch (cause) {
    if (isCurrent()) referenceError.value = cause instanceof Error ? cause.message : '纳入结果未确认，请刷新后核实；重复引用不会重复计数'
  } finally {
    if (isCurrent()) referencing.value = null
  }
}
const starting = ref(false)
const startError = ref('')
type PendingStart = { key: string; body: string }
const pendingStart = ref<PendingStart | null>(null)
const startBlocked = ref(false)
const blockedStarts = new Set<string>()
const storageWarning = '原启动身份无法恢复：待确认信息损坏或会话存储不可用，无法安全判断原请求。已阻断新启动；调查详情仍可读取。请保留当前会话并联系维护者核验原请求，不要清除存储或放弃后新开。'
const uuidPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const startStorageKey = (id: string) => `logscope:pending-start:${id.toLowerCase()}`
function parseStart(saved: string): PendingStart {
  const pending = JSON.parse(saved)
  if (!pending || typeof pending !== 'object' || Array.isArray(pending) ||
    Object.keys(pending).sort().join(',') !== 'body,key' ||
    typeof pending.key !== 'string' || !uuidPattern.test(pending.key) || typeof pending.body !== 'string') throw new Error(storageWarning)
  const payload = JSON.parse(pending.body)
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
    typeof payload.service_id !== 'string' || !payload.service_id.trim() || payload.service_id.length > 80 ||
    Object.keys(payload).some(key => !['service_id', 'starts_at', 'ends_at'].includes(key))) throw new Error(storageWarning)
  if ('starts_at' in payload || 'ends_at' in payload) {
    const absolute = /^(\d{4})-(\d{2})-(\d{2})T([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d)(?:[.,](\d+))?)?([zZ]|([+-])([01]\d|2[0-3]):?([0-5]\d))$/
    const parseAbsolute = (value: unknown): bigint | null => {
      const parts = typeof value === 'string' ? absolute.exec(value) : null
      if (!parts) return null
      const year = Number(parts[1]), month = Number(parts[2]), day = Number(parts[3])
      const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
      const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
      if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]!) return null
      const date = new Date(0)
      date.setUTCFullYear(year, month - 1, day)
      date.setUTCHours(Number(parts[4]), Number(parts[5]), Number(parts[6] ?? 0), 0)
      const offset = parts[9] ? (parts[9] === '+' ? 1 : -1) * (Number(parts[10]) * 60 + Number(parts[11])) : 0
      // Pydantic按微秒截断小数；只计算校验值，绝不改写原请求body。
      return BigInt(date.getTime() - offset * 60000) * 1000n + BigInt((parts[7] ?? '').slice(0, 6).padEnd(6, '0'))
    }
    const starts = parseAbsolute(payload.starts_at), ends = parseAbsolute(payload.ends_at)
    if (starts === null || ends === null || !(ends > starts && ends - starts <= 86400000000n)) throw new Error(storageWarning)
  }
  return pending
}
function restoreStart(id: string) {
  pendingStart.value = null
  startBlocked.value = false
  try {
    if (blockedStarts.has(id)) throw new Error(storageWarning)
    const saved = sessionStorage.getItem(startStorageKey(id))
    pendingStart.value = saved === null ? null : parseStart(saved)
  } catch {
    blockedStarts.add(id)
    startBlocked.value = true
  }
  const payload = pendingStart.value ? JSON.parse(pendingStart.value.body) : null
  serviceId.value = payload?.service_id ?? ''
  rangeMode.value = payload?.starts_at ? 'absolute' : 'recent'
  startsAt.value = payload?.starts_at ?? ''
  endsAt.value = payload?.ends_at ?? ''
}
function clearStart(id: string, pending: PendingStart) {
  try {
    const saved = sessionStorage.getItem(startStorageKey(id))
    if (saved !== null) {
      const stored = JSON.parse(saved)
      if (stored?.key === pending.key && stored?.body === pending.body) {
        sessionStorage.removeItem(startStorageKey(id))
        blockedStarts.delete(id)
        return true
      }
    }
  } catch {
    // 迟到回调只记录所属调查的故障，不污染当前页面。
    blockedStarts.add(id)
  }
}

const investigations = ref<Investigation[]>([])
const selected = ref<Investigation | null>(null)
const searchQuery = ref('')
const loading = ref(false)
const error = ref('')
const creating = ref(false)
const saving = ref(false)
const title = ref('')
const description = ref('')
const formError = ref('')
// 网络结果不明时保留同一内容的身份，显式重试不会重复创建。
let pendingRequest: { key: string; body: string } | null = null
let readSequence = 0

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/investigations${path}`, options)
  if (!response.ok) {
    const problem = await response.json().catch(() => null)
    const detail = problem?.detail
    if (typeof detail === 'string') throw new Error(detail)
    if (Array.isArray(detail)) throw new Error(detail.map(item => item.msg).join('；'))
    throw new Error('读取或保存失败，请检查连接后重试')
  }
  return response.json() as Promise<T>
}

async function loadPage() {
  const sequence = ++readSequence
  loading.value = true
  error.value = ''
  selected.value = null
  canceling.value = null
  cancelError.value = ''
  referencing.value = null
  referenceError.value = ''
  pendingStart.value = null
  startBlocked.value = false
  starting.value = false
  serviceId.value = ''
  rangeMode.value = 'recent'
  startsAt.value = ''
  endsAt.value = ''
  startError.value = ''
  try {
    const id = location.hash.match(/^#\/investigations\/([a-f0-9-]+)$/i)?.[1]?.toLowerCase()
    if (id) {
      restoreStart(id)
      const record = await request<Investigation>(`/${id}`)
      if (sequence === readSequence) selected.value = record
    } else {
      const result = await request<{ items: Investigation[]; total: number }>(`?q=${encodeURIComponent(searchQuery.value)}`)
      if (sequence === readSequence) investigations.value = result.items
    }
  } catch (cause) {
    if (sequence === readSequence) error.value = cause instanceof Error ? cause.message : '请求失败'
  } finally {
    if (sequence === readSequence) loading.value = false
  }
}

async function loadServices() {
  try {
    const response = await fetch('/api/services')
    if (!response.ok) throw new Error('服务目录读取失败')
    services.value = (await response.json()).items
  } catch {
    startError.value = '服务目录读取失败，无法启动；请刷新重试'
  }
}

async function startInvestigation() {
  if (starting.value || !selected.value || startBlocked.value) return
  if (!pendingStart.value && (!serviceId.value || !services.value.some(service => service.id === serviceId.value))) return
  const id = selected.value.id
  const payload = rangeMode.value === 'recent' ? { service_id: serviceId.value } : {
    service_id: serviceId.value, starts_at: startsAt.value, ends_at: endsAt.value,
  }
  const pending = pendingStart.value ?? { key: crypto.randomUUID(), body: JSON.stringify(payload) }
  const route = location.hash
  const sequence = readSequence
  const isCurrent = () => sequence === readSequence && location.hash === route && selected.value?.id === id
  starting.value = true
  startError.value = ''
  try {
    // 先持久化身份再发送；存储不可用时不发送无法跨刷新确认的请求。
    try {
      sessionStorage.setItem(startStorageKey(id), JSON.stringify(pending))
    } catch {
      blockedStarts.add(id)
      if (isCurrent()) startBlocked.value = true
      return
    }
    pendingStart.value = pending
    const response = await fetch(`/api/investigations/${id}/attempts`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pending.key }, body: pending.body,
    })
    if (!response.ok) {
      const problem = await response.json().catch(() => null)
      // 明确的校验/接纳拒绝允许修正输入；网络错误与5xx仍须同键确认。
      if ([404, 409, 422].includes(response.status)) {
        const cleared = clearStart(id, pending)
        if (isCurrent() && cleared) {
          pendingStart.value = null
          startBlocked.value = false
        } else if (isCurrent() || (selected.value?.id === id && (pendingStart.value?.key === pending.key || cleared))) restoreStart(id)
      }
      const detail = problem?.detail
      throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => item.msg).join('；') : '启动结果未确认，请重试原请求')
    }
    const record = await response.json() as Investigation
    clearStart(id, pending)
    if (isCurrent()) {
      selected.value = record
      restoreStart(id)
    } else if (selected.value?.id === id && pendingStart.value?.key === pending.key) {
      restoreStart(id)
    }
  } catch (cause) {
    if (isCurrent()) startError.value = cause instanceof Error ? cause.message : '启动结果未确认，请重试原请求'
  } finally {
    if (isCurrent()) starting.value = false
  }
}

function openDetail(id: string) {
  location.hash = `/investigations/${id}`
}

function backToList() {
  location.hash = '/'
}

function openCreate() {
  title.value = ''
  description.value = ''
  formError.value = ''
  pendingRequest = null
  creating.value = true
}

async function saveInvestigation() {
  if (saving.value) return
  if (!title.value.trim()) {
    formError.value = '请输入调查标题'
    return
  }
  const body = JSON.stringify({ title: title.value.trim(), description: description.value.trim() })
  if (!pendingRequest || pendingRequest.body !== body) pendingRequest = { key: crypto.randomUUID(), body }
  saving.value = true
  formError.value = ''
  try {
    const record = await request<Investigation>('', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pendingRequest.key }, body,
    })
    creating.value = false
    pendingRequest = null
    openDetail(record.id)
  } catch (cause) {
    formError.value = cause instanceof Error ? cause.message : '保存失败，请重试'
  } finally {
    saving.value = false
  }
}

const dateFormatter = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
})
function displayTime(value: string) {
  return `${dateFormatter.format(new Date(value))} UTC+8`
}
onMounted(() => {
  window.addEventListener('hashchange', loadPage)
  void loadPage()
  void loadServices()
})
onUnmounted(() => window.removeEventListener('hashchange', loadPage))
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#/" aria-label="LogScope 首页"><span class="brand-mark"><TerminalSquare :size="19" /></span><span class="brand-name">Log<span>Scope</span></span><span class="brand-version">0.1</span></a>
      <div class="workspace-switcher"><div class="workspace-avatar">L</div><div class="workspace-copy"><strong>调查工作区</strong><span>本地开发环境</span></div></div>
      <nav class="main-nav" aria-label="主导航"><button class="nav-item active" @click="backToList"><Command :size="17" />调查工作台</button></nav>
      <div class="nav-label recent-label">当前列表</div>
      <nav class="recent-nav" aria-label="最近调查"><button v-for="item in investigations.slice(0, 3)" :key="item.id" class="recent-item" @click="openDetail(item.id)"><span class="recent-title">{{ item.title }}</span></button></nav>
    </aside>
    <main class="main-content">
      <header class="topbar"><div class="breadcrumbs"><span>LogScope</span><span class="crumb-divider">/</span><strong>调查工作台</strong></div><span>北京时间 UTC+8</span></header>
      <div class="page-content">
        <section class="page-heading"><div><div class="eyebrow"><ShieldCheck :size="14" />只读调查模式</div><h1>调查工作台</h1><p class="page-subtitle">先登记问题，创建后保持待处理。</p></div><el-button type="primary" :icon="Plus" @click="openCreate">新建调查</el-button></section>
        <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
        <p v-if="loading" role="status">正在读取调查记录…</p>
        <section v-else-if="selected" class="investigation-section detail-panel" aria-label="调查详情">
          <el-button @click="backToList">返回列表</el-button>
          <el-button @click="loadPage">刷新调查状态</el-button>
          <el-alert v-if="cancelError" :title="cancelError" type="error" :closable="false" />
          <h2>{{ selected.title }}</h2>
          <p class="incident-id">{{ selected.id }}</p>
          <p class="description-copy">{{ selected.description || '未填写问题描述' }}</p>
          <dl class="detail-grid">
            <div><dt>状态</dt><dd>{{ selected.status }}</dd></div>
            <div><dt>严重程度</dt><dd>{{ selected.severity }}<template v-if="selected.severity_assessed_at"> · {{ selected.severity_source === 'human' ? '人工评估' : 'Agent评估' }}于 {{ displayTime(selected.severity_assessed_at) }}；保留等级不代表已验证当前影响。</template><template v-else> · 尚未评估，不表示低风险</template></dd></div>
            <div><dt>被调查服务</dt><dd>{{ selected.service || '未选择服务' }}</dd></div>
            <div><dt>证据</dt><dd>证据条数：{{ selected.evidence_count }}</dd></div>
            <div><dt>创建时间</dt><dd>{{ displayTime(selected.created_at) }}</dd></div>
            <div><dt>最后更新时间</dt><dd>{{ displayTime(selected.updated_at) }}</dd></div>
            <div><dt>保留至</dt><dd>{{ selected.retention_expires_at ? displayTime(selected.retention_expires_at) : '运行中不清理；结束后计算保留期' }}</dd></div>
          </dl>
          <p v-if="startBlocked" role="alert" class="form-error">{{ storageWarning }}</p>
          <h3>调查尝试</h3>
          <template v-if="selected.attempts.length">
            <nav aria-label="逐轮导航"><el-button v-for="attempt in selected.attempts" :key="attempt.id" @click="locateAttempt(attempt.id)">第{{ attempt.attempt_number }}轮 · {{ attempt.status }}</el-button></nav>
            <section v-for="attempt in selected.attempts" :id="`attempt-${attempt.id}`" :key="attempt.id" :aria-label="`第${attempt.attempt_number}轮${attempt.id === selected.attempts.at(-1)?.id ? '当前' : '历史'}`">
              <h3>第{{ attempt.attempt_number }}轮 · {{ attempt.id === selected.attempts.at(-1)?.id ? '当前轮' : '历史（只读）' }}</h3>
              <p>服务：{{ attempt.service_id }} · {{ attempt.status }}</p>
              <el-button v-if="attempt.status === '调查中'" :disabled="canceling === attempt.id || attempt.execution_phase === 'stopping' || attempt.execution_phase === 'timeout_stopping' || attempt.execution_phase === 'confirming_execution'" @click="cancelInvestigation(attempt.id)">取消调查</el-button>
              <p v-if="attempt.execution_phase === 'confirming_execution'" role="status">正在确认执行状态：尚未可靠确认旧执行退出，仍为调查中、继续占用运行名额，不能启动新轮。已隔离旧执行写入，请刷新调查状态查看确认结果。</p>
              <p v-if="attempt.execution_phase === 'stopping'" role="status">正在停止：尚未确认执行终止，仍为调查中、继续占用运行名额，不能启动新轮。请刷新调查状态查看确认结果。</p>
              <p>单轮截止时间：{{ displayTime(attempt.deadline_at) }}；启动起10分钟，查询、重试和等待共享预算。</p>
              <p v-if="attempt.execution_phase === 'timeout_stopping'" role="status">总时限已到，等待执行退出确认：仍为调查中、继续占用运行名额，不能启动新轮。请刷新调查状态查看确认结果。</p>
              <p v-if="attempt.stop_confirmed_at && attempt.cancel_requested_at">用户主动停止，已确认执行终止；停止前保存的材料仍保留。</p>
              <p v-if="attempt.stop_confirmed_at && attempt.timeout_requested_at">总时限退出已确认，已保存材料仍保留。</p>
              <p>固定日志范围：{{ displayTime(attempt.starts_at) }} — {{ displayTime(attempt.ends_at) }}</p>
              <p>可控执行适配器：支持受控异步查询及结构化证据；未接真实日志或 Agent，不代表真实来源验收完成。</p>
              <p v-if="attempt.ended_at">结束时间：{{ displayTime(attempt.ended_at) }} · {{ attempt.end_reason === 'user_canceled' ? '用户主动停止，已确认执行终止' : attempt.end_reason === 'necessary_sources_unavailable' ? '必要来源不可用，无法继续' : attempt.end_reason === 'attempt_deadline_exceeded' ? '单轮10分钟总时限已到' : attempt.end_reason === 'service_interrupted' ? '服务中断，已确认旧执行退出并隔离写入' : '正常排查结束' }}</p>
              <p>调查结果：{{ attempt.result || (attempt.status === '已取消' ? '主动停止，未完成排查' : attempt.status === '失败' ? '技术失败，未完成排查' : '尚未结束') }}；已完成不代表已确认根因。</p>
              <h4>来源查询与覆盖</h4>
              <p role="status">{{ attempt.query_coverage.notice }}</p>
              <p v-if="!attempt.queries.length">尚未查询所有来源，覆盖不完整。</p>
              <p v-if="attempt.query_coverage.missing_sources.length">缺失来源：{{ attempt.query_coverage.missing_sources.join('、') }}</p>
              <article v-for="query in attempt.queries" :key="query.id" class="evidence-card" aria-label="来源查询">
                <p>{{ query.status === 'running' ? '查询尚未退出' : '查询已退出' }}</p>
                <div v-for="source in query.sources" :key="source.source">
                  <p>{{ source.source }} · {{ queryStates[source.state] }} · 实际查询次数：{{ source.attempts }}</p>
                  <p v-if="source.reason">原因：{{ queryReasons[source.reason] }}</p>
                  <p v-if="source.retry_after_seconds">来源要求 Retry-After：{{ source.retry_after_seconds }} 秒；不提前绕过限制。</p>
                  <p v-for="failure in source.failures" :key="failure.attempt">故障 {{ failure.attempt }}：{{ queryReasons[failure.reason] }}<span v-if="failure.wait_seconds !== null">；退出后等待 {{ failure.wait_seconds }} 秒再试</span></p>
                </div>
              </article>
              <h4>当前结论</h4>
              <p v-if="attempt.conclusion">版本 {{ attempt.conclusion.version }} · {{ conclusionLabels[attempt.conclusion.kind] }}</p>
              <p v-else>尚无结论。</p>
              <article v-for="conclusion in attempt.conclusions" :key="conclusion.id" aria-label="结论版本" class="evidence-card">
                <h4>结论版本 {{ conclusion.version }} · {{ conclusionLabels[conclusion.kind] }}</h4>
                <p>{{ displayTime(conclusion.created_at) }} · 核验状态：{{ conclusion.verification === 'supported_controlled_trace' ? '受控轨迹支持（非普遍证明）' : '证据不足' }}</p>
                <p v-if="conclusion.mechanism">候选机制：依赖错误传播导致请求失败</p>
                <p v-if="conclusion.phenomenon">故障现象：{{ conclusion.phenomenon }}</p>
                <p v-if="conclusion.causal_chain">因果机制：{{ conclusion.causal_chain }}</p>
                <p v-if="conclusion.alternatives">替代解释：{{ conclusion.alternatives }}</p>
                <p v-if="conclusion.contradictions">关键矛盾：{{ conclusion.contradictions }}</p>
                <p>现有依据：<span v-if="!conclusion.evidence_ids.length">无已纳入证据，不表示没有故障。</span></p>
                <p v-for="id in conclusion.evidence_ids" :key="id"><a :href="`#evidence-${attempt.id}-${id}`" @click.prevent="locateEvidence(attempt.id, id)">查看证据 {{ id }}</a></p>
                <p v-if="conclusion.missing.length">缺失证据：{{ conclusion.missing.map(item => missingLabels[item]).join('、') }}</p>
                <p>{{ conclusion.boundary }}</p>
                <p>修复建议仅供参考，不执行环境写操作。</p>
              </article>
              <h4>脱敏证据</h4>
              <p v-if="referenceTarget() && attempt.id !== referenceTarget()?.id">历史证据须显式纳入，服务、固定时间范围与核验材料由服务端核验；引用不表示本轮覆盖完整。</p>
              <p v-if="referenceError && attempt.id === selected.attempts.at(-1)?.id" role="alert">{{ referenceError }}</p>
              <p>本轮证据条数：{{ attempt.evidence_count }}；可靠身份重复只计一次，缺少可靠身份逐条保留。不表示日志扫描量、故障数、根因数或覆盖完整率。</p>
              <p v-if="!attempt.evidence.length">尚无已保存证据。</p>
              <p v-for="notice in attempt.evidence_notices" :key="notice" role="status">{{ notice }}</p>
              <article v-for="item in attempt.evidence" :id="`evidence-${attempt.id}-${item.id}`" :key="item.id" aria-label="脱敏证据" class="evidence-card">
                <p>来源轮次：第{{ item.source_attempt_number }}轮 <button @click="locateAttempt(item.source_attempt_id)">查看来源轮</button></p>
                <p>{{ item.verification === 'retained_structured_material' ? '已保留受控脱敏结构化核验材料' : '不可核验：缺少最低核验材料' }}；代号定位不能直接打开原链接。</p>
                <template v-if="referenceTarget() && item.source_attempt_id === attempt.id && attempt.id !== referenceTarget()?.id">
                  <el-button :disabled="!!referencing || (!!historicalState(item.id)?.included_evidence_id && !!historicalState(item.id)?.reference_allowed)" @click="includeHistorical(item.id)">{{ historicalState(item.id)?.included_evidence_id && historicalState(item.id)?.reference_allowed ? '已纳入本轮' : '纳入本轮' }}</el-button>
                  <p v-if="historicalState(item.id)?.included_evidence_id && !historicalState(item.id)?.reference_allowed">本轮已等价纳入其他核验材料；此旧材料仍不符合引用条件，不能据此纳入。</p>
                </template>
                <dl>
                  <dt>来源</dt><dd>{{ item.source }}</dd>
                  <dt>发生时间</dt><dd>{{ displayTime(item.occurred_at) }}</dd>
                  <dt>查询条件</dt><dd>{{ displayTime(item.query.starts_at) }} — {{ displayTime(item.query.ends_at) }}<pre>{{ JSON.stringify(item.query.fields, null, 2) }}</pre></dd>
                  <dt>脱敏核验材料</dt><dd><pre>{{ JSON.stringify(item.snippet, null, 2) }}</pre></dd>
                  <dt>来源定位</dt><dd>{{ item.locator || '未提供定位；可核验材料为上述脱敏结构化片段' }}</dd>
                  <dt>脱敏说明</dt><dd>{{ item.redaction_note }}</dd>
                </dl>
              </article>
            </section>
          </template>
          <p v-else>尚无调查尝试，未启动排查。</p>
          <template v-if="pendingStart || !selected.attempts.some(attempt => attempt.status === '调查中')">
            <h3 v-if="pendingStart">启动结果待确认</h3>
            <h3 v-else-if="selected.attempts.length">手动发起新一轮</h3>
            <p v-if="pendingStart" role="status">原请求的服务和范围已锁定。刷新或看到终态不代表已确认本次启动；请重试原请求并读取当前结果，不会额外创建一轮。确认成功后才可显式发起下一轮。待确认信息仅保存在当前标签页会话，请勿清除站点存储或关闭标签页。</p>
            <p v-if="selected.attempts.length">历史轮次只读保留；新轮不自动继承历史证据、结论或查询覆盖，无累计次数上限。</p>
            <p role="status">实例最多同时运行3个调查，正在停止及正在确认执行状态仍占名额；满额不排队，未启动时请稍后手动重试。</p>
            <form @submit.prevent="startInvestigation">
              <p><label for="start-service">被调查服务</label> <select id="start-service" v-model="serviceId" :disabled="starting || !!pendingStart || startBlocked"><option value="">请选择已配置服务</option><option v-for="service in services" :key="service.id" :value="service.id">{{ service.name }}</option></select></p>
              <p v-if="!services.length">尚无可选服务，请由维护者配置后端服务目录。</p>
              <p><label for="range-mode">范围模式</label> <select id="range-mode" v-model="rangeMode" :disabled="starting || !!pendingStart || startBlocked"><option value="recent">最近1小时（启动时由服务器计算）</option><option value="absolute">绝对起止时间（最多24小时）</option></select></p>
              <template v-if="rangeMode === 'absolute'">
                <p><label for="starts-at">日志开始时间</label> <input id="starts-at" v-model="startsAt" :disabled="starting || !!pendingStart || startBlocked" placeholder="2026-10-30T08:00:00+08:00" /></p>
                <p><label for="ends-at">日志结束时间</label> <input id="ends-at" v-model="endsAt" :disabled="starting || !!pendingStart || startBlocked" placeholder="2026-10-31T08:00:00+08:00" /></p>
                <p>请输入带时区的绝对时间；恰好24小时允许。</p>
              </template>
              <p v-if="startError" role="alert" class="form-error">{{ startError }}</p>
              <el-button type="primary" native-type="submit" :loading="starting" :disabled="startBlocked || (!pendingStart && (!serviceId || !services.some(service => service.id === serviceId)))">{{ pendingStart ? '重试原请求并确认结果' : selected.attempts.length ? '启动新一轮' : '启动调查' }}</el-button>
            </form>
            <p class="page-subtitle">无轮记录从创建起保留 30 天；查看不会延长期限。</p>
          </template>
        </section>
        <section v-else-if="!error" class="investigation-section">
          <div class="section-heading"><div><h2>调查记录</h2><p>按最后更新时间排序</p></div><el-button @click="loadPage">刷新列表</el-button></div>
          <form class="table-toolbar" @submit.prevent="loadPage"><el-input v-model="searchQuery" placeholder="搜索调查标题或编号" clearable class="search-input" /><el-button native-type="submit">搜索</el-button></form>
          <div class="table-wrap"><el-table :data="investigations" row-key="id" empty-text="没有匹配的调查记录">
            <el-table-column label="调查" min-width="260"><template #default="{ row }"><button class="incident-title" @click="openDetail(row.id)">{{ row.title }}</button><div class="incident-id">{{ row.id }}</div></template></el-table-column>
            <el-table-column label="服务" min-width="130"><template #default="{ row }">{{ row.service || '未选择服务' }}</template></el-table-column>
            <el-table-column prop="severity" label="严重程度" width="100" />
            <el-table-column prop="status" label="状态" width="100" />
            <el-table-column label="证据条数" width="140"><template #default="{ row }">{{ row.evidence_count }}<div v-if="row.attempts.at(-1)?.evidence_notices.some((notice: string) => notice.startsWith('可能重复'))" class="incident-id">可能重复</div></template></el-table-column>
            <el-table-column label="最后更新时间" min-width="240"><template #default="{ row }">{{ displayTime(row.updated_at) }}</template></el-table-column>
          </el-table></div>
          <div class="table-footer">显示 {{ investigations.length }} 条调查记录</div>
        </section>
        <el-button v-if="error" @click="loadPage">重试读取</el-button>
        <footer class="page-footer"><span>LogScope</span><span>创建记录不启动调查 · 未评估不表示低风险</span></footer>
      </div>
    </main>
    <el-dialog v-model="creating" title="新建待处理调查" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" width="min(560px, 94vw)">
      <form @submit.prevent="saveInvestigation">
        <label for="investigation-title">调查标题</label>
        <el-input id="investigation-title" v-model="title" maxlength="200" :disabled="saving" />
        <label for="investigation-description" class="description-label">问题描述</label>
        <el-input id="investigation-description" v-model="description" type="textarea" :rows="4" maxlength="4000" :disabled="saving" />
        <p>创建后为待处理、严重程度未评估，不创建运行轮。</p>
        <p v-if="formError" role="alert" class="form-error">{{ formError }}</p>
        <el-button :disabled="saving" @click="creating = false">取消</el-button>
        <el-button type="primary" native-type="submit" :loading="saving">保存待处理调查</el-button>
      </form>
    </el-dialog>
  </div>
</template>

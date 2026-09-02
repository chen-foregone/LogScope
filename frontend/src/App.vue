<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Plus, ShieldCheck, TerminalSquare, Command } from 'lucide-vue-next'

type Investigation = {
  id: string
  title: string
  description: string
  service: string | null
  status: '待处理'
  severity: '未评估'
  evidence_count: number
  attempts: unknown[]
  created_at: string
  updated_at: string
  retention_expires_at: string
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
    if (response.status === 404) throw new Error('调查不存在')
    if (response.status === 409) throw new Error('请求标识冲突，请重新打开创建表单')
    if (response.status === 422) throw new Error('输入不符合要求，请检查标题和描述')
    throw new Error('读取或保存失败，请检查连接后重试')
  }
  return response.json() as Promise<T>
}

async function loadPage() {
  const sequence = ++readSequence
  loading.value = true
  error.value = ''
  selected.value = null
  try {
    const id = location.hash.match(/^#\/investigations\/([a-f0-9-]+)$/i)?.[1]
    if (id) {
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
          <h2>{{ selected.title }}</h2>
          <p class="incident-id">{{ selected.id }}</p>
          <p class="description-copy">{{ selected.description || '未填写问题描述' }}</p>
          <dl class="detail-grid">
            <div><dt>状态</dt><dd>{{ selected.status }}</dd></div>
            <div><dt>严重程度</dt><dd>{{ selected.severity }}</dd></div>
            <div><dt>被调查服务</dt><dd>{{ selected.service || '未选择服务' }}</dd></div>
            <div><dt>证据</dt><dd>证据条数：{{ selected.evidence_count }}</dd></div>
            <div><dt>创建时间</dt><dd>{{ displayTime(selected.created_at) }}</dd></div>
            <div><dt>最后更新时间</dt><dd>{{ displayTime(selected.updated_at) }}</dd></div>
            <div><dt>保留至</dt><dd>{{ displayTime(selected.retention_expires_at) }}</dd></div>
          </dl>
          <h3>调查尝试</h3><p>尚无调查尝试，未启动排查。</p>
          <p class="page-subtitle">无轮记录从创建起保留 30 天；查看不会延长期限。</p>
        </section>
        <section v-else-if="!error" class="investigation-section">
          <div class="section-heading"><div><h2>调查记录</h2><p>按最后更新时间排序</p></div><el-button @click="loadPage">刷新列表</el-button></div>
          <form class="table-toolbar" @submit.prevent="loadPage"><el-input v-model="searchQuery" placeholder="搜索调查标题或编号" clearable class="search-input" /><el-button native-type="submit">搜索</el-button></form>
          <div class="table-wrap"><el-table :data="investigations" row-key="id" empty-text="没有匹配的调查记录">
            <el-table-column label="调查" min-width="260"><template #default="{ row }"><button class="incident-title" @click="openDetail(row.id)">{{ row.title }}</button><div class="incident-id">{{ row.id }}</div></template></el-table-column>
            <el-table-column label="服务" min-width="130"><template #default="{ row }">{{ row.service || '未选择服务' }}</template></el-table-column>
            <el-table-column prop="severity" label="严重程度" width="100" />
            <el-table-column prop="status" label="状态" width="100" />
            <el-table-column prop="evidence_count" label="证据条数" width="100" />
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

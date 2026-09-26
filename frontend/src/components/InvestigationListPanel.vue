<script setup lang="ts">
import type { InvestigationPage } from '../api/investigations'
import type { Draft } from '../composables/useInvestigationList'
import InvestigationFilters from './InvestigationFilters.vue'
import InvestigationOverview from './InvestigationOverview.vue'
import InvestigationTable from './InvestigationTable.vue'
defineProps<{ draft: Draft; data: InvestigationPage | null; busy: boolean; error: string; services: { id: string; name: string }[]; directoryError: boolean }>()
defineEmits<{ 'update:draft': [Draft]; apply: []; refresh: []; page: [number]; open: [string] }>()
const timeFormatter = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
function displayTime(value: string) { return timeFormatter.format(new Date(value)) + ' UTC+8' }
</script>
<template><section class="investigation-section" :aria-busy="busy">
  <div class="section-heading"><div><h2>调查记录</h2><p>按最后更新时间排序 · 服务取最新轮</p></div><el-button :loading="busy" @click="$emit('refresh')">刷新列表</el-button></div>
  <InvestigationFilters :model-value="draft" :services="services" :busy="busy" @update:model-value="$emit('update:draft', $event)" @apply="$emit('apply')" />
  <p v-if="directoryError" role="alert">服务目录读取失败；列表仍可读取，请刷新页面重试目录。</p>
  <p v-if="error" role="alert">{{ error }}{{ data ? '；当前为上次结果' : '' }}</p>
  <p v-if="busy" role="status">正在读取调查及概况…</p>
  <template v-if="data"><InvestigationOverview :overview="data.overview" /><p class="page-subtitle">概况覆盖全部匹配调查。证据条数不代表扫描量、故障数或根因数。</p>
    <p v-if="data.filter.updated_from" class="page-subtitle">更新时间范围（左闭右开）：{{ displayTime(data.filter.updated_from) }} — {{ displayTime(data.filter.updated_before!) }}；翻页保持边界，刷新重新计算相对范围。</p>
    <InvestigationTable :data="data" :busy="busy" @open="$emit('open', $event)" @page="$emit('page', $event)" /></template>
</section></template>

<script setup lang="ts">
import type { Draft } from '../composables/useInvestigationList'
defineProps<{ modelValue: Draft; services: { id: string; name: string }[]; busy: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [Draft]; apply: [] }>()
function update(current: Draft, field: keyof Draft, event: Event) {
  emit('update:modelValue', { ...current, [field]: (event.target as HTMLInputElement).value })
}
</script>
<template>
  <form class="listing-filters" @submit.prevent="emit('apply')">
    <el-input :model-value="modelValue.q" maxlength="200" placeholder="搜索调查标题或编号" aria-label="搜索标题、编号或当前服务ID" @update:model-value="emit('update:modelValue', { ...modelValue, q: $event })" />
    <label>服务<select aria-label="服务" :value="modelValue.service" @change="update(modelValue, 'service', $event)"><option value="">全部服务</option><option v-if="modelValue.service && !services.some(s => s.id === modelValue.service)" :value="modelValue.service">{{ modelValue.service }}</option><option v-for="s in services" :key="s.id" :value="s.id">{{ s.name }}</option></select></label>
    <label>最后更新时间<select aria-label="最后更新时间" :value="modelValue.mode" @change="update(modelValue, 'mode', $event)"><option value="all">全部时间</option><option value="1h">最近1小时</option><option value="24h">最近24小时</option><option value="7d">最近7天</option><option value="fixed">固定范围 UTC+8</option></select></label>
    <template v-if="modelValue.mode === 'fixed'"><label>开始 UTC+8<input type="datetime-local" step="1" :value="modelValue.from" @input="update(modelValue, 'from', $event)" /></label><label>结束 UTC+8（不含）<input type="datetime-local" step="1" :value="modelValue.before" @input="update(modelValue, 'before', $event)" /></label></template>
    <el-button native-type="submit" :loading="busy">搜索</el-button>
  </form>
</template>

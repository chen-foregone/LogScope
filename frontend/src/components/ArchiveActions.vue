<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
const props = defineProps<{ investigationId: string; archivedAt: string | null; version: number; running: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const busy = ref(false)
const error = ref('')
let mounted = true
onUnmounted(() => { mounted = false })
async function change() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  const route = location.hash
  try {
    const response = await fetch(`/api/investigations/${props.investigationId}/${props.archivedAt ? 'unarchive' : 'archive'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_version: props.version }),
    })
    if (!mounted || location.hash !== route) return
    if (!response.ok) {
      error.value = response.status === 409 ? '状态已变化或仍有运行工作，请刷新调查后重试。' :
        response.status === 404 ? '调查已不存在，无法恢复或操作。' : '操作结果未确认，请刷新调查核实后再操作。'
      return
    }
    emit('changed')
  } catch {
    error.value = '操作结果未确认，请刷新调查核实后再操作。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section aria-label="调查归档操作" :aria-busy="busy" class="archive-actions">
    <p>归档后 30 天将永久清除全部调查材料，不留标题或记录；源日志不受影响。可在清除前取消归档。</p>
    <p v-if="archivedAt">归档时间：{{ new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(archivedAt)) }}（UTC+8）</p>
    <p v-if="archivedAt">归档后须先取消归档，才能启动新轮。</p>
    <el-button :loading="busy" :disabled="running || busy" @click="change">{{ archivedAt ? '取消归档' : '归档调查' }}</el-button>
    <p v-if="running">运行工作结束前不能归档。</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.archive-actions { margin: 16px 0; padding: 16px; border: 1px solid #d5dfdf; border-radius: 10px; overflow-wrap: anywhere; }
.archive-actions p { line-height: 1.6; }
</style>

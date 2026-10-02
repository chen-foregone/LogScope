<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
const props = defineProps<{ investigationId: string; running: boolean }>()
const emit = defineEmits<{ removed: [] }>()
type Preview = { scope_version: string; counts: Record<string, number>; irreversible: boolean }
const preview = ref<Preview | null>(null)
const busy = ref(false), confirmed = ref(false), error = ref('')
let mounted = true
onUnmounted(() => { mounted = false })
const labels: Record<string, string> = {
  investigations: '调查记录', attempts: '逐轮尝试', evidence: '脱敏证据', attempt_evidence: '证据纳入与引用',
  conclusions: '结论版本', evidence_checks: '证据核验历史', conclusion_checks: '结论核验历史',
  severity_events: '严重程度变更与建议', controlled_queries: '查询历史', controlled_executions: '执行身份',
  investigation_redaction_keys: '调查脱敏密钥',
}
async function act(remove: boolean) {
  if (busy.value || (remove && (!preview.value || !confirmed.value))) return
  busy.value = true; error.value = ''
  const route = location.hash
  try {
    const response = await fetch(`/api/investigations/${props.investigationId}/${remove ? 'cleanup' : 'cleanup-preview'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      ...(remove ? { body: JSON.stringify({ scope_version: preview.value!.scope_version, confirm: true }) } : {}),
    })
    if (!mounted || location.hash !== route) return
    if (!response.ok) {
      preview.value = null; confirmed.value = false
      if (response.status === 404) {
        error.value = '调查已不存在，无法证明此前删除请求是否成功；不能恢复。'
      } else if (response.status === 409) {
        const result = await response.json()
        error.value = typeof result.detail === 'string' ? result.detail : '范围或运行状态变化，请重新预览。'
      } else error.value = '清除结果未确认，请刷新核查，不要视为成功。'
      return
    }
    if (remove) emit('removed')
    else { preview.value = await response.json(); confirmed.value = false }
  } catch {
    error.value = '清除结果未确认，请刷新核查，不要视为成功。'
  } finally { busy.value = false }
}
</script>

<template>
  <section aria-label="永久删除调查" class="cleanup-actions" :aria-busy="busy">
    <h3>永久删除</h3>
    <p>无需先归档。只删除本调查产物，不修改源日志；其他调查仍需材料时整次拒绝。</p>
    <el-button :disabled="busy || running" :loading="busy" @click="act(false)">预览删除范围</el-button>
    <template v-if="preview">
      <h4>本次实际清除范围</h4>
      <ul><li v-for="(count, name) in preview.counts" :key="name">{{ labels[name] ?? name }}：{{ count }}</li></ul>
      <p>不可恢复：全部永久清除，不保留标题、记录或删除回执。</p>
      <label><input v-model="confirmed" type="checkbox" :disabled="busy" />我理解不可恢复，确认删除以上范围</label>
      <el-button type="danger" :disabled="busy || !confirmed" :loading="busy" @click="act(true)">永久删除调查</el-button>
      <el-button :disabled="busy" @click="preview = null; confirmed = false">放弃删除</el-button>
    </template>
    <p v-if="running">运行中不能删除。</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.cleanup-actions { margin: 16px 0; padding: 16px; border: 1px solid #be7770; border-radius: 10px; overflow-wrap: anywhere; }
.cleanup-actions p, .cleanup-actions li { line-height: 1.6; }
.cleanup-actions label { display: block; margin: 16px 0; }
</style>

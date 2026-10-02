<script setup lang="ts">
defineProps<{
  retention: { phase: 'running' | 'retained' | 'archived' | 'expiring' | 'awaiting_cleanup' | 'inconsistent'; issue: string | null }
  expiresAt: string | null
}>()
const labels = {
  running: '运行保护', retained: '未归档 · 持续保留', archived: '已归档', expiring: '归档即将到期',
  awaiting_cleanup: '归档已到期', inconsistent: '保留状态异常',
}
function displayTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value))
}
</script>

<template>
  <section class="retention-status" data-testid="retention-status" aria-label="材料保留状态" role="status">
    <strong>{{ labels[retention.phase] }}</strong>
    <p v-if="retention.phase === 'running'">运行中不清理；结束后不自动起算清除期限。</p>
    <p v-else-if="retention.phase === 'inconsistent'">{{ retention.issue }}</p>
    <p v-else-if="expiresAt">归档清除期限 {{ displayTime(expiresAt) }}（UTC+8）</p>
    <p v-if="retention.phase === 'expiring'">将在 7 天内到期，可在清除前取消归档。</p>
    <p v-if="retention.phase === 'awaiting_cleanup'">已达到归档清除期限；当前材料仍可读取不代表期限已延长。</p>
    <p v-if="retention.phase === 'awaiting_cleanup' && retention.issue" role="alert">自动清除受阻：{{ retention.issue }}</p>
    <p>未归档无自动清除期限；查看不续期。阶段以最近一次成功读取为准。</p>
  </section>
</template>

<style scoped>
.retention-status { margin: 16px 0; padding: 16px; border: 1px solid #d5dfdf; border-radius: 10px; background: #f5f8f7; overflow-wrap: anywhere; }
.retention-status p { margin: 8px 0 0; line-height: 1.6; }
</style>

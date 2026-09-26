<script setup lang="ts">
defineProps<{
  retention: { phase: 'running' | 'retained' | 'expiring' | 'awaiting_cleanup' | 'inconsistent'; issue: string | null }
  expiresAt: string | null
}>()
const labels = {
  running: '运行保护', retained: '正常保留', expiring: '即将到期',
  awaiting_cleanup: '待确认清理', inconsistent: '保留状态异常',
}
function displayTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value))
}
</script>

<template>
  <section class="retention-status" data-testid="retention-status" aria-label="材料保留状态" role="status">
    <strong>{{ labels[retention.phase] }}</strong>
    <p v-if="retention.phase === 'running'">运行中不清理；结束后重新计算保留期限。</p>
    <p v-else-if="retention.phase === 'inconsistent'">{{ retention.issue }}</p>
    <p v-else-if="expiresAt">保留至 {{ displayTime(expiresAt) }}（UTC+8）</p>
    <p v-if="retention.phase === 'expiring'">将在 7 天内到期；到期后须由用户确认才清理。</p>
    <p v-if="retention.phase === 'awaiting_cleanup'">已到期，未确认清理，材料仍保留；不会自动续期或删除。</p>
    <p>查看不延长期限；首版不提供延期。阶段以最近一次成功读取为准，刷新详情可更新。</p>
  </section>
</template>

<style scoped>
.retention-status { margin: 16px 0; padding: 16px; border: 1px solid #d5dfdf; border-radius: 10px; background: #f5f8f7; overflow-wrap: anywhere; }
.retention-status p { margin: 8px 0 0; line-height: 1.6; }
</style>

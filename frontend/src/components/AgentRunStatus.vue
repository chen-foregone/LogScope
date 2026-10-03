<script setup lang="ts">
defineProps<{ run: { model: string; phase: string; reason: string | null; calls: number; spent_microyuan: number; reserved_microyuan: number } }>()
const phases: Record<string, string> = { pending: '待领取', claimed: '已领取', analyzing: '模型分析中', querying: '只读查询中', completed: '已完成', stopped: '已停止', failed: '执行失败' }
const reasons: Record<string, string> = { normal_completion: '正常结束', model_error: '模型调用或产物校验失败', budget_exhausted: '累计预算不足', tool_rejected: '工具请求不被允许', input_limit: '输入材料达到上限', call_limit: '达到调用次数上限', execution_fenced: '执行已隔离', process_exited: '已确认进程退出', service_stopping: '服务正在停止' }
</script>
<template>
  <section aria-label="Agent运行状态" class="evidence-card">
    <h4>只读 Agent · {{ run.model }}</h4>
    <p>阶段：{{ phases[run.phase] ?? run.phase }} · 模型调用 {{ run.calls }} 次</p>
    <p>估算已结算 ¥{{ (run.spent_microyuan / 1000000).toFixed(6) }} · 待确认预留 ¥{{ (run.reserved_microyuan / 1000000).toFixed(6) }}</p>
    <p v-if="run.reason">{{ reasons[run.reason] ?? '执行已结束，请核查记录' }}</p>
    <p role="note">模拟日志来源，非真实来源验收。费用以百炼账单为准；建议不会自动执行。</p>
  </section>
</template>

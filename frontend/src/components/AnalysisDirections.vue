<script setup lang="ts">
defineProps<{ modelCategory: string; directions: { category: string; evidence_ids: string[] }[] }>()
defineEmits<{ locate: [id: string] }>()
const labels: Record<string, string> = { dependency: '依赖异常', resource: '资源问题', configuration: '配置问题', permission: '权限问题', application: '应用逻辑', unknown: '未知原因' }
</script>

<template>
  <section aria-label="分析分类依据">
    <p>模型候选分类（未验证）：{{ labels[modelCategory] ?? modelCategory }}</p>
    <p v-if="!directions.length">现有引用不足以支持具体故障方向。</p>
    <div v-for="direction in directions" :key="direction.category">
      <p>观测支持的方向：{{ labels[direction.category] ?? direction.category }}（不代表根因）</p>
      <button v-for="id in direction.evidence_ids" :key="id" class="text-link" type="button" @click="$emit('locate', id)">核查分类证据 {{ id }}</button>
    </div>
  </section>
</template>

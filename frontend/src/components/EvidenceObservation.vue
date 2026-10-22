<script setup lang="ts">
defineProps<{ observation: { component: string; signal: string; http_status?: number; used?: number; capacity?: number; unit?: string } }>()
const components: Record<string, string> = { gateway: '网关', application: '订单应用', database: '数据库', redis: 'Redis' }
const signals: Record<string, string> = { http_response: 'HTTP 响应', request_timeout: '请求超时', dependency_timeout: '依赖超时', pool_capacity: '连接池占用', configuration_missing: '配置缺失', permission_denied: '权限拒绝' }
</script>

<template>
  <div aria-label="结构化观测">
    <p>组件：{{ components[observation.component] ?? observation.component }} · 观测：{{ signals[observation.signal] ?? observation.signal }}</p>
    <p v-if="observation.http_status !== undefined">HTTP 状态：{{ observation.http_status }}</p>
    <p v-if="observation.used !== undefined">连接数：{{ observation.used }} / {{ observation.capacity }}</p>
    <p role="note">观测描述现象，不单独证明根因。</p>
  </div>
</template>

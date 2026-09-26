<script setup lang="ts">
import type { Overview } from '../api/investigations'
defineProps<{ overview: Overview }>()
</script>
<template><dl class="listing-overview" aria-label="匹配调查概况"><div><dt>调查总数</dt><dd>{{ overview.investigation_count }}</dd></div><div><dt>活跃调查数</dt><dd>{{ overview.active_count }}</dd></div><div><dt>待处理调查数</dt><dd>{{ overview.pending_count }}</dd></div><div><dt>证据总条数</dt><dd>{{ overview.evidence_count }}</dd></div></dl></template>

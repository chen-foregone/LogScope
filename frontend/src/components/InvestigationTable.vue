<script setup lang="ts">
import type { InvestigationPage } from '../api/investigations'
defineProps<{ data: InvestigationPage; busy: boolean }>()
const emit = defineEmits<{ open: [string]; page: [number] }>()
const formatter = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
</script>
<template>
  <div class="table-wrap"><el-table :data="data.items" row-key="id" empty-text="没有匹配的调查记录">
    <el-table-column label="调查" min-width="260"><template #default="{ row }"><button class="incident-title" @click="emit('open', row.id)">{{ row.title }}</button><div class="incident-id">{{ row.id }}</div></template></el-table-column>
    <el-table-column label="服务" min-width="130"><template #default="{ row }">{{ row.service || '未选择服务' }}</template></el-table-column>
    <el-table-column prop="severity" label="严重程度" width="100" /><el-table-column prop="status" label="状态" width="100" />
    <el-table-column label="证据条数" width="140"><template #default="{ row }">{{ row.evidence_count }}<div v-if="row.possible_duplicate_evidence">可能重复</div></template></el-table-column>
    <el-table-column label="最后更新时间" min-width="240"><template #default="{ row }">{{ formatter.format(new Date(row.updated_at)) }} UTC+8</template></el-table-column>
  </el-table></div>
  <div class="table-footer"><span>显示 {{ data.items.length }} 条调查记录，共 {{ data.total }} 条</span><el-pagination layout="prev, pager, next" :total="data.total" :page-size="data.page_size" :current-page="data.page" :disabled="busy" @current-change="emit('page', $event)" /></div>
</template>

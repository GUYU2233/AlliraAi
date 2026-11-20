<script setup lang="ts">
import { onMounted, onScopeDispose, ref } from 'vue';
import { ElAlert, ElButton, ElTable, ElTableColumn, ElTag } from 'element-plus';
import type { AdminAuditEntry } from '@allira/client';
import { useAdminView, useAdminQuery, formatDate, formatNumber } from '../context';

const ctx = useAdminView();
const online = ref(navigator.onLine !== false);
const audit = useAdminQuery<AdminAuditEntry>((cursor, signal) => ctx.client.listAudit({ cursor, limit: 50, signal }));

function updateOnline(): void {
  online.value = navigator.onLine !== false;
}

async function refreshAudit(explicit = true): Promise<void> {
  if (!ctx.valid() || audit.loading.value || ctx.mutating.value || !online.value) return;
  const committed = await audit.refresh();
  if (committed && ctx.valid() && explicit) ctx.reconcile();
}

onMounted(() => {
  window.addEventListener('online', updateOnline);
  window.addEventListener('offline', updateOnline);
  void refreshAudit(false);
});
onScopeDispose(() => {
  window.removeEventListener('online', updateOnline);
  window.removeEventListener('offline', updateOnline);
});
</script>

<template>
  <section aria-labelledby="audit-heading">
    <div class="page-heading">
      <div>
        <h1 id="audit-heading">管理审计</h1>
        <p class="section-subtitle">仅展示管理 API 的公开审计字段：时间、操作、目标类型和目标 ID。</p>
      </div>
      <ElButton :loading="audit.loading.value" :disabled="!online || ctx.mutating.value" @click="refreshAudit()">刷新审计记录</ElButton>
    </div>
    <ElAlert title="审计为只读摘要，不展示内部 owner 字段、聊天正文、完整兑换码或认证凭据。核对后若要再次执行修改，仍需单独确认。" type="info" :closable="false" show-icon />
    <ElAlert v-if="!online" title="当前离线，无法刷新审计。" type="warning" :closable="false" show-icon />
    <ElAlert v-if="audit.error.value" :title="audit.error.value" type="error" :closable="false" show-icon />

    <div class="data-card audit-card">
      <ElTable :data="audit.items.value" row-key="id" empty-text="暂无审计记录。" :aria-busy="audit.loading.value" table-layout="auto">
        <ElTableColumn label="时间" min-width="190">
          <template #default="{ row }">{{ formatDate(row.createdAt) }}</template>
        </ElTableColumn>
        <ElTableColumn label="操作" min-width="250">
          <template #default="{ row }"><span class="mono resource-id">{{ row.action }}</span></template>
        </ElTableColumn>
        <ElTableColumn label="目标类型" min-width="160">
          <template #default="{ row }"><ElTag type="info" effect="plain">{{ row.targetType }}</ElTag></template>
        </ElTableColumn>
        <ElTableColumn label="目标 ID" min-width="300">
          <template #default="{ row }"><span class="mono resource-id">{{ row.targetId ?? '—' }}</span></template>
        </ElTableColumn>
      </ElTable>
      <div class="table-footer">
        <span class="muted" role="status">已显示 {{ formatNumber(audit.items.value.length) }} 条审计记录</span>
        <ElButton :loading="audit.loading.value" :disabled="!audit.nextCursor.value || !online || ctx.mutating.value" @click="audit.loadMore()">加载更多审计记录</ElButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
.audit-card { margin-top: 22px; }
.resource-id { overflow-wrap: anywhere; }
</style>

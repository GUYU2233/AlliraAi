<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, shallowRef, watch } from 'vue';
import { ElAlert, ElButton, ElDescriptions, ElDescriptionsItem, ElDialog, ElForm, ElFormItem, ElInput, ElOption, ElSelect, ElTable, ElTableColumn, ElTag } from 'element-plus';
import type { AdminRun, Run } from '@allira/client';
import { useAdminView, useAdminQuery, formatDate, formatNumber } from '../context';
import DangerConfirm from '../components/DangerConfirm.vue';

const ctx = useAdminView();
const ownerId = ref('');
const status = ref<Run['status'] | ''>('');
const filterDirty = ref(false);
const online = ref(navigator.onLine !== false);
const cancelTarget = shallowRef<AdminRun | null>(null);
const statusLoading = ref<Record<string, boolean>>({});
const statusRequests = new Map<string, AbortController>();
let filterEpoch = 0;

const runs = useAdminQuery<AdminRun>((cursor, signal) => {
  const userId = ownerId.value.trim();
  return ctx.client.listRuns({ cursor, limit: 30, ...(userId ? { userId } : {}), ...(status.value ? { status: status.value } : {}), signal });
});
const writeDisabled = computed(() => ctx.mutating.value || ctx.blocked.value || runs.loading.value || !online.value);

function updateOnline(): void {
  online.value = navigator.onLine !== false;
}

function stopStatusRequests(): void {
  for (const controller of statusRequests.values()) controller.abort();
  statusRequests.clear();
  statusLoading.value = {};
}

function closeCancel(): void {
  cancelTarget.value = null;
}

function resetLocal(): void {
  stopStatusRequests();
  closeCancel();
}

watch([ownerId, status], () => {
  ++filterEpoch;
  filterDirty.value = true;
  runs.invalidate();
  resetLocal();
}, { flush: 'sync' });

async function refreshRuns(explicit = true): Promise<void> {
  if (!ctx.valid() || runs.loading.value || ctx.mutating.value || !online.value) return;
  const epoch = ++filterEpoch;
  resetLocal();
  const committed = await runs.refresh();
  if (!committed || !ctx.valid() || epoch !== filterEpoch) return;
  filterDirty.value = false;
  if (explicit) ctx.reconcile();
}

function applyRun(updated: AdminRun): void {
  runs.items.value = runs.items.value.map(item => item.id === updated.id ? updated : item);
  if (cancelTarget.value?.id === updated.id) {
    cancelTarget.value = updated.status === 'running' ? updated : null;
  }
}

async function refreshRun(item: AdminRun): Promise<void> {
  if (!ctx.valid() || filterDirty.value || runs.loading.value || ctx.mutating.value || !online.value || statusRequests.has(item.id)) return;
  const epoch = filterEpoch;
  const controller = new AbortController();
  statusRequests.set(item.id, controller);
  statusLoading.value = { ...statusLoading.value, [item.id]: true };
  try {
    const updated = await ctx.client.getRun(item.id, { signal: controller.signal });
    if (!ctx.valid() || controller.signal.aborted || epoch !== filterEpoch || statusRequests.get(item.id) !== controller) return;
    applyRun(updated);
    ctx.notify('已读取服务端任务状态；此页面不会轮询或自动恢复生成。', 'info');
  } catch (error: unknown) {
    if (ctx.valid() && !controller.signal.aborted && epoch === filterEpoch && statusRequests.get(item.id) === controller) ctx.fail(error);
  } finally {
    if (statusRequests.get(item.id) === controller) {
      statusRequests.delete(item.id);
      const remaining = { ...statusLoading.value };
      delete remaining[item.id];
      statusLoading.value = remaining;
    }
  }
}

function chooseCancel(item: AdminRun): void {
  if (!ctx.valid() || writeDisabled.value || filterDirty.value || runs.loading.value || statusRequests.has(item.id) || item.status !== 'running') return;
  cancelTarget.value = item;
}

async function cancelRun(): Promise<void> {
  const target = cancelTarget.value;
  if (!target || target.status !== 'running' || writeDisabled.value || !ctx.valid()) return;
  const epoch = filterEpoch;
  // A late GET snapshot must not overwrite the explicit cancellation result.
  stopStatusRequests();
  closeCancel();
  await ctx.mutate(() => ctx.client.cancelRun(target.id, { signal: ctx.signal }), updated => {
    if (!ctx.valid() || epoch !== filterEpoch) return;
    applyRun(updated);
    ctx.notify(`停止请求已返回：${runStatus(updated.status)}（服务端真实状态）。`, updated.status === 'running' ? 'warning' : 'success');
  });
}

function runStatus(value: unknown): string {
  switch (value) {
    case 'running': return '生成中';
    case 'completed': return '已完成';
    case 'failed': return '失败';
    case 'cancelled': return '已停止';
    default: return '未知';
  }
}

function runTone(value: unknown): 'success' | 'warning' | 'danger' | 'info' {
  if (value === 'running') return 'warning';
  if (value === 'completed') return 'success';
  if (value === 'failed') return 'danger';
  return 'info';
}

function onCancelVisibility(visible: boolean): void {
  if (!visible) closeCancel();
}

ctx.signal.addEventListener('abort', resetLocal, { once: true });
onMounted(() => {
  window.addEventListener('online', updateOnline);
  window.addEventListener('offline', updateOnline);
  void refreshRuns(false);
});
onScopeDispose(() => {
  ++filterEpoch;
  resetLocal();
  ctx.signal.removeEventListener('abort', resetLocal);
  window.removeEventListener('online', updateOnline);
  window.removeEventListener('offline', updateOnline);
});
</script>

<template>
  <section aria-labelledby="runs-heading">
    <div class="page-heading">
      <div>
        <h1 id="runs-heading">生成任务</h1>
        <p class="section-subtitle">按所有者和任务状态检索，显式读取最新状态；仅确认后才停止正在运行的任务。</p>
      </div>
      <ElButton :loading="runs.loading.value" :disabled="!online || ctx.mutating.value" @click="refreshRuns()">刷新任务列表</ElButton>
    </div>

    <ElForm class="toolbar" inline aria-label="任务筛选" @submit.prevent="refreshRuns()">
      <ElFormItem label="所有者 ID">
        <ElInput v-model="ownerId" name="runUserId" placeholder="留空查看全部所有者" maxlength="100" clearable :disabled="ctx.mutating.value" />
      </ElFormItem>
      <ElFormItem label="任务状态">
        <ElSelect v-model="status" name="status" aria-label="任务状态" class="status-filter" :disabled="ctx.mutating.value">
          <ElOption label="全部状态" value="" />
          <ElOption label="生成中" value="running" />
          <ElOption label="已完成" value="completed" />
          <ElOption label="失败" value="failed" />
          <ElOption label="已停止" value="cancelled" />
        </ElSelect>
      </ElFormItem>
      <ElFormItem>
        <ElButton type="primary" native-type="submit" :loading="runs.loading.value" :disabled="!online || ctx.mutating.value">查询任务</ElButton>
      </ElFormItem>
      <span v-if="filterDirty" class="muted" role="status">筛选已更改，请查询以加载列表。</span>
    </ElForm>

    <ElAlert v-if="!online" title="当前离线，停止任务已停用。联网后请显式刷新核对状态。" type="warning" :closable="false" show-icon />
    <ElAlert v-if="runs.error.value" :title="runs.error.value" type="error" :closable="false" show-icon />

    <div class="data-card">
      <ElTable :data="runs.items.value" row-key="id" empty-text="暂无任务，或尚未加载当前筛选。" :aria-busy="runs.loading.value" table-layout="auto">
        <!-- @vue-generic {AdminRun} -->
        <ElTableColumn label="任务 ID / 会话 ID" min-width="260">
          <template #default="{ row }">
            <div class="mono resource-id">{{ row.id }}</div>
            <div class="mono muted resource-id">{{ row.conversationId }}</div>
          </template>
        </ElTableColumn>
        <!-- @vue-generic {AdminRun} -->
        <ElTableColumn label="所有者" min-width="220">
          <template #default="{ row }"><span class="mono resource-id">{{ row.ownerId }}</span></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminRun} -->
        <ElTableColumn label="状态" min-width="110">
          <template #default="{ row }"><ElTag :type="runTone(row.status)" effect="light">{{ runStatus(row.status) }}</ElTag></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminRun} -->
        <ElTableColumn label="更新时间" min-width="180">
          <template #default="{ row }">{{ formatDate(row.updatedAt) }}</template>
        </ElTableColumn>
        <!-- @vue-generic {AdminRun} -->
        <ElTableColumn label="操作" min-width="220" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" :loading="statusLoading[row.id] === true" :disabled="filterDirty || runs.loading.value || ctx.mutating.value || !online" @click="refreshRun(row)">刷新任务状态</ElButton>
            <ElButton v-if="row.status === 'running'" link type="danger" :disabled="writeDisabled || filterDirty || runs.loading.value || statusLoading[row.id] === true" @click="chooseCancel(row)">停止任务</ElButton>
            <span v-else class="muted">无停止操作</span>
          </template>
        </ElTableColumn>
      </ElTable>
      <div class="table-footer">
        <span class="muted" role="status">已显示 {{ formatNumber(runs.items.value.length) }} 条任务 · 不自动轮询</span>
        <ElButton :loading="runs.loading.value" :disabled="!runs.nextCursor.value || filterDirty || !online || ctx.mutating.value" @click="runs.loadMore()">加载更多任务</ElButton>
      </div>
    </div>

    <ElDialog :model-value="cancelTarget !== null" title="停止生成任务" width="min(580px, 94vw)" destroy-on-close :close-on-click-modal="false" @update:model-value="onCancelVisibility">
      <template v-if="cancelTarget">
        <ElDescriptions :column="1" border>
          <ElDescriptionsItem label="任务 ID"><span class="mono resource-id">{{ cancelTarget.id }}</span></ElDescriptionsItem>
          <ElDescriptionsItem label="会话 ID"><span class="mono resource-id">{{ cancelTarget.conversationId }}</span></ElDescriptionsItem>
          <ElDescriptionsItem label="所有者"><span class="mono resource-id">{{ cancelTarget.ownerId }}</span></ElDescriptionsItem>
          <ElDescriptionsItem label="最近状态">{{ runStatus(cancelTarget.status) }}</ElDescriptionsItem>
        </ElDescriptions>
        <div class="confirm-section">
          <DangerConfirm :key="cancelTarget.id" title="确认停止此生成任务" description="这会向服务端发送一次停止请求，不会自动重试或重新生成。最终状态以服务端返回为准；关闭页面本身不会停止任务。" confirm-label="确认停止任务" :disabled="writeDisabled || cancelTarget.status !== 'running'" :busy="ctx.mutating.value" @confirm="cancelRun" />
        </div>
      </template>
    </ElDialog>
  </section>
</template>

<style scoped>
.status-filter { width: 160px; }
.resource-id { overflow-wrap: anywhere; }
.confirm-section { margin-top: 22px; }
</style>

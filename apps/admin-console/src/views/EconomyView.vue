<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, shallowRef, watch } from 'vue';
import { ElAlert, ElButton, ElCard, ElDescriptions, ElDescriptionsItem, ElDialog, ElForm, ElFormItem, ElInputNumber, ElTable, ElTableColumn, ElTag } from 'element-plus';
import type { AdminCdk, AdminUsageOverview, IssueCdksInput, IssuedCdk } from '@allira/client';
import { useAdminView, useAdminQuery, formatDate, formatNumber, yesNo } from '../context';
import DangerConfirm from '../components/DangerConfirm.vue';

const props = defineProps<{ view: 'cdks' | 'usage' }>();
const ctx = useAdminView();
const online = ref(navigator.onLine !== false);
const energy = ref<number | undefined>(100);
const count = ref<number | undefined>(1);
const expiresInDays = ref<number | undefined>(30);
const pendingIssue = shallowRef<Required<IssueCdksInput> | null>(null);
const disableTarget = shallowRef<AdminCdk | null>(null);
const issuedCodes = shallowRef<IssuedCdk[]>([]);
const usage = shallowRef<AdminUsageOverview | null>(null);
const usageLoading = ref(false);
const usageError = ref('');
let usageController: AbortController | null = null;
let viewEpoch = 0;

const cdks = useAdminQuery<AdminCdk>((cursor, signal) => ctx.client.listCdks({ cursor, limit: 30, signal }));
const writeDisabled = computed(() => ctx.mutating.value || ctx.blocked.value || cdks.loading.value || !online.value);
const validIssue = computed(() => integerInRange(energy.value, 1, 1_000_000) && integerInRange(count.value, 1, 50) && integerInRange(expiresInDays.value, 1, 365));
const totalIssueEnergy = computed(() => validIssue.value ? Number(energy.value) * Number(count.value) : null);

function integerInRange(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;
}

function updateOnline(): void {
  online.value = navigator.onLine !== false;
}

function clearIssued(): void {
  // No browser storage, clipboard writes, logging or replayable read path for plaintext codes.
  issuedCodes.value = [];
}

function closeIssue(): void {
  pendingIssue.value = null;
}

function closeDisable(): void {
  disableTarget.value = null;
}

function clearTransient(): void {
  clearIssued();
  closeIssue();
  closeDisable();
}

function stopUsage(): void {
  usageController?.abort();
  usageController = null;
  usageLoading.value = false;
}

async function refreshCdks(explicit = true): Promise<void> {
  if (!ctx.valid() || props.view !== 'cdks' || cdks.loading.value || ctx.mutating.value || !online.value) return;
  const epoch = viewEpoch;
  closeDisable();
  const committed = await cdks.refresh();
  if (committed && ctx.valid() && epoch === viewEpoch && props.view === 'cdks' && explicit) ctx.reconcile();
}

function prepareIssue(): void {
  if (!ctx.valid() || props.view !== 'cdks' || writeDisabled.value) return;
  if (!integerInRange(energy.value, 1, 1_000_000) || !integerInRange(count.value, 1, 50) || !integerInRange(expiresInDays.value, 1, 365)) {
    ctx.notify('请填写有效整数：每码能量 1–1,000,000、数量 1–50、有效天数 1–365。', 'warning');
    return;
  }
  pendingIssue.value = { energy: energy.value, count: count.value, expiresInDays: expiresInDays.value };
}

async function issueCdks(): Promise<void> {
  const input = pendingIssue.value;
  if (!input || !ctx.valid() || props.view !== 'cdks' || writeDisabled.value) return;
  const epoch = viewEpoch;
  closeIssue();
  clearIssued();
  await ctx.mutate(() => ctx.client.issueCdks(input, { signal: ctx.signal }), async result => {
    if (!ctx.valid() || props.view !== 'cdks' || epoch !== viewEpoch) return;
    issuedCodes.value = result.items;
    ctx.notify('兑换码已签发。完整码仅本次可见，丢失不补发；请立即安全保存。', 'success');
    // Metadata refresh must never replay issuance, reconstruct a code, or reconcile a blocked write.
    await cdks.refresh();
  });
}

function chooseDisable(item: AdminCdk): void {
  if (!ctx.valid() || props.view !== 'cdks' || writeDisabled.value || cdks.loading.value || item.disabled || item.redeemedBy) return;
  disableTarget.value = item;
}

async function disableCdk(): Promise<void> {
  const target = disableTarget.value;
  if (!target || target.disabled || target.redeemedBy || !ctx.valid() || props.view !== 'cdks' || writeDisabled.value) return;
  const epoch = viewEpoch;
  closeDisable();
  await ctx.mutate(() => ctx.client.disableCdk(target.prefix, { signal: ctx.signal }), async () => {
    if (!ctx.valid() || props.view !== 'cdks' || epoch !== viewEpoch) return;
    cdks.items.value = cdks.items.value.map(item => item.prefix === target.prefix ? { ...item, disabled: true } : item);
    ctx.notify('此前缀的兑换码已停用。', 'success');
    await cdks.refresh();
  });
}

async function refreshUsage(): Promise<void> {
  if (!ctx.valid() || props.view !== 'usage' || usageLoading.value || ctx.mutating.value || !online.value) return;
  const epoch = viewEpoch;
  const controller = new AbortController();
  usageController = controller;
  usageLoading.value = true;
  usageError.value = '';
  usage.value = null;
  try {
    const result = await ctx.client.getUsageOverview({ signal: controller.signal });
    if (!ctx.valid() || controller.signal.aborted || props.view !== 'usage' || epoch !== viewEpoch || usageController !== controller) return;
    usage.value = result;
    // Aggregate usage cannot reconcile an uncertain issuance or another resource mutation.
  } catch (error: unknown) {
    if (ctx.valid() && !controller.signal.aborted && props.view === 'usage' && epoch === viewEpoch && usageController === controller) {
      usageError.value = '能量用量读取失败，请稍后显式刷新。';
      ctx.fail(error);
    }
  } finally {
    if (usageController === controller) {
      usageController = null;
      usageLoading.value = false;
    }
  }
}

function loadView(): void {
  if (props.view === 'cdks') void refreshCdks(false);
  else void refreshUsage();
}

watch(() => props.view, () => {
  ++viewEpoch;
  cdks.invalidate();
  stopUsage();
  clearTransient();
  usage.value = null;
  usageError.value = '';
  loadView();
}, { flush: 'sync' });

// Editing a form invalidates any separately confirmed snapshot of its old values.
watch([energy, count, expiresInDays], closeIssue, { flush: 'sync' });

function expiryDate(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '未知';
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? '未知' : formatDate(date.toISOString());
}

function cdkStatus(item: AdminCdk): string {
  if (item.disabled) return '已停用';
  if (item.redeemedBy) return '已兑换';
  if (typeof item.expiresAt !== 'number' || !Number.isFinite(item.expiresAt)) return '有效期未知';
  return item.expiresAt * 1000 <= Date.now() ? '已过期' : '待兑换';
}

function cdkTone(item: AdminCdk): 'success' | 'warning' | 'danger' | 'info' {
  if (item.disabled) return 'danger';
  if (item.redeemedBy) return 'success';
  return cdkStatus(item) === '待兑换' ? 'warning' : 'info';
}

function onIssueVisibility(visible: boolean): void {
  if (!visible) closeIssue();
}

function onDisableVisibility(visible: boolean): void {
  if (!visible) closeDisable();
}

function onAbort(): void {
  ++viewEpoch;
  clearTransient();
  stopUsage();
  usage.value = null;
}

ctx.signal.addEventListener('abort', onAbort, { once: true });
onMounted(() => {
  window.addEventListener('online', updateOnline);
  window.addEventListener('offline', updateOnline);
  loadView();
});
onScopeDispose(() => {
  onAbort();
  ctx.signal.removeEventListener('abort', onAbort);
  window.removeEventListener('online', updateOnline);
  window.removeEventListener('offline', updateOnline);
});
</script>

<template>
  <section v-if="view === 'cdks'" aria-labelledby="cdks-heading">
    <div class="page-heading">
      <div>
        <h1 id="cdks-heading">兑换码管理</h1>
        <p class="section-subtitle">摘要列表不含完整码。签发必须单独确认，完整码仅在当前签发结果中显示一次。</p>
      </div>
      <ElButton :loading="cdks.loading.value" :disabled="!online || ctx.mutating.value" @click="refreshCdks()">刷新兑换码列表</ElButton>
    </div>
    <ElAlert v-if="!online" title="当前离线，签发和停用均已锁定。联网后请显式刷新核对状态。" type="warning" :closable="false" show-icon />

    <ElCard shadow="never" class="issue-card">
      <template #header><strong>签发兑换码</strong></template>
      <ElAlert title="完整兑换码丢失不补发，也无法从摘要列表恢复。新一批签发会清空上一批明文；关闭结果、切走本页或退出管理后均清空。" type="warning" :closable="false" show-icon />
      <ElForm class="issue-form" label-position="top" aria-label="兑换码签发参数" @submit.prevent="prepareIssue">
        <div class="issue-fields">
          <ElFormItem label="每码整数能量（1–1,000,000）">
            <ElInputNumber v-model="energy" name="cdkEnergy" :min="1" :max="1000000" :step="1" step-strictly :precision="0" :disabled="writeDisabled" controls-position="right" />
          </ElFormItem>
          <ElFormItem label="签发数量（1–50）">
            <ElInputNumber v-model="count" name="cdkCount" :min="1" :max="50" :step="1" step-strictly :precision="0" :disabled="writeDisabled" controls-position="right" />
          </ElFormItem>
          <ElFormItem label="有效天数（1–365）">
            <ElInputNumber v-model="expiresInDays" name="cdkDays" :min="1" :max="365" :step="1" step-strictly :precision="0" :disabled="writeDisabled" controls-position="right" />
          </ElFormItem>
        </div>
        <div class="toolbar">
          <ElButton type="primary" native-type="submit" :disabled="writeDisabled || !validIssue" :loading="ctx.mutating.value">核对并签发兑换码</ElButton>
          <span class="muted">本批总能量：{{ formatNumber(totalIssueEnergy) }} · 仅提交能量、数量、有效天数</span>
        </div>
      </ElForm>
    </ElCard>

    <ElCard v-if="issuedCodes.length" shadow="never" class="issued-card" aria-label="本次签发的完整兑换码">
      <template #header>
        <div class="issued-heading"><strong>本次完整兑换码 · 仅显示这一次</strong><ElButton type="danger" plain @click="clearIssued">清空完整兑换码</ElButton></div>
      </template>
      <p class="muted">请立即安全保存；丢失不补发。以下是本次显式签发返回的原文，不写入浏览器存储或日志。</p>
      <div v-for="item in issuedCodes" :key="item.prefix" class="issued-code">
        <pre class="mono code-plaintext">{{ item.code }}</pre>
        <p class="muted">每码能量 {{ formatNumber(item.energy) }} · 到期 {{ expiryDate(item.expiresAt) }}</p>
      </div>
    </ElCard>

    <div class="page-heading compact-heading">
      <div><h2>兑换码摘要</h2><p class="section-subtitle">仅展示公开前缀及兑换状态，不会请求或恢复历史完整码。</p></div>
    </div>
    <ElAlert v-if="cdks.error.value" :title="cdks.error.value" type="error" :closable="false" show-icon />
    <div class="data-card">
      <ElTable :data="cdks.items.value" row-key="prefix" empty-text="暂无兑换码摘要。" :aria-busy="cdks.loading.value" table-layout="auto">
        <!-- @vue-generic {AdminCdk} -->
        <ElTableColumn label="前缀" min-width="150">
          <template #default="{ row }"><span class="mono resource-id">{{ row.prefix }}</span></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminCdk} -->
        <ElTableColumn label="能量" min-width="110" align="right">
          <template #default="{ row }">{{ formatNumber(row.energy) }}</template>
        </ElTableColumn>
        <!-- @vue-generic {AdminCdk} -->
        <ElTableColumn label="到期时间" min-width="180">
          <template #default="{ row }">{{ expiryDate(row.expiresAt) }}</template>
        </ElTableColumn>
        <!-- @vue-generic {AdminCdk} -->
        <ElTableColumn label="状态" min-width="120">
          <template #default="{ row }"><ElTag :type="cdkTone(row)" effect="light">{{ cdkStatus(row) }}</ElTag></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminCdk} -->
        <ElTableColumn label="兑换用户 / 时间" min-width="220">
          <template #default="{ row }"><div class="mono resource-id">{{ row.redeemedBy ?? '—' }}</div><div class="muted">{{ formatDate(row.redeemedAt) }}</div></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminCdk} -->
        <ElTableColumn label="操作" min-width="140" fixed="right">
          <template #default="{ row }">
            <ElButton v-if="!row.disabled && !row.redeemedBy" link type="danger" :disabled="writeDisabled || cdks.loading.value" @click="chooseDisable(row)">停用兑换码</ElButton>
            <span v-else class="muted">无需操作</span>
          </template>
        </ElTableColumn>
      </ElTable>
      <div class="table-footer">
        <span class="muted" role="status">已显示 {{ formatNumber(cdks.items.value.length) }} 个摘要 · 不含完整码</span>
        <ElButton :loading="cdks.loading.value" :disabled="!cdks.nextCursor.value || !online || ctx.mutating.value" @click="cdks.loadMore()">加载更多兑换码</ElButton>
      </div>
    </div>

    <ElDialog :model-value="pendingIssue !== null" title="核对兑换码签发" width="min(580px, 94vw)" destroy-on-close :close-on-click-modal="false" @update:model-value="onIssueVisibility">
      <template v-if="pendingIssue">
        <ElDescriptions :column="1" border>
          <ElDescriptionsItem label="每码能量">{{ formatNumber(pendingIssue.energy) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="签发数量">{{ formatNumber(pendingIssue.count) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="有效天数">{{ formatNumber(pendingIssue.expiresInDays) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="总能量">{{ formatNumber(pendingIssue.energy * pendingIssue.count) }}</ElDescriptionsItem>
        </ElDescriptions>
        <div class="confirm-section">
          <DangerConfirm title="确认按以上参数签发兑换码" description="这会执行一次真实管理签发。完整码只在这次结果中显示，丢失不补发；任何结果不确定的请求均不会自动重试。" confirm-label="确认签发兑换码" :disabled="writeDisabled" :busy="ctx.mutating.value" @confirm="issueCdks" />
        </div>
      </template>
    </ElDialog>

    <ElDialog :model-value="disableTarget !== null" title="停用兑换码" width="min(560px, 94vw)" destroy-on-close :close-on-click-modal="false" @update:model-value="onDisableVisibility">
      <template v-if="disableTarget">
        <ElDescriptions :column="1" border>
          <ElDescriptionsItem label="将被停用的前缀"><span class="mono resource-id">{{ disableTarget.prefix }}</span></ElDescriptionsItem>
          <ElDescriptionsItem label="每码能量">{{ formatNumber(disableTarget.energy) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="当前已停用">{{ yesNo(disableTarget.disabled) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="到期时间">{{ expiryDate(disableTarget.expiresAt) }}</ElDescriptionsItem>
        </ElDescriptions>
        <div class="confirm-section">
          <DangerConfirm :key="disableTarget.prefix" title="确认停用此前缀的兑换码" description="服务端会按此前缀停用兑换码，之后不能再用于兑换。该操作不包含自动重新启用，也不会重新签发完整码。" confirm-label="确认停用兑换码" :disabled="writeDisabled" :busy="ctx.mutating.value" @confirm="disableCdk" />
        </div>
      </template>
    </ElDialog>
  </section>

  <section v-else aria-labelledby="usage-heading">
    <div class="page-heading">
      <div>
        <h1 id="usage-heading">能量用量</h1>
        <p class="section-subtitle">已计量的业务任务、字符和整数能量；页面打开时读取一次，之后仅手动刷新。</p>
      </div>
      <ElButton :loading="usageLoading" :disabled="!online || ctx.mutating.value" @click="refreshUsage()">刷新能量用量统计</ElButton>
    </div>
    <ElAlert title="统计单位是字符与整数能量，不是 Token 计数，不代表真实供应商报价、利润、账单或生产容量验证。" type="info" :closable="false" show-icon />
    <ElAlert v-if="!online" title="当前离线，无法刷新统计。" type="warning" :closable="false" show-icon />
    <ElAlert v-if="usageError" :title="usageError" type="error" :closable="false" show-icon />
    <ElAlert v-if="usage && usage.measurement !== 'characters_not_tokens'" title="服务端计量口径未知，请勿按 Token 或真实报价解释。" type="warning" :closable="false" show-icon />
    <div class="metric-grid usage-metrics" :aria-busy="usageLoading">
      <ElCard shadow="never"><p class="muted">已计量任务数</p><strong class="metric-value">{{ formatNumber(usage?.runs) }}</strong></ElCard>
      <ElCard shadow="never"><p class="muted">预留能量</p><strong class="metric-value">{{ formatNumber(usage?.reservedEnergy) }}</strong></ElCard>
      <ElCard shadow="never"><p class="muted">已结算能量</p><strong class="metric-value">{{ formatNumber(usage?.settledEnergy) }}</strong></ElCard>
      <ElCard shadow="never"><p class="muted">已退回能量</p><strong class="metric-value">{{ formatNumber(usage?.refundedEnergy) }}</strong></ElCard>
      <ElCard shadow="never"><p class="muted">输入字符</p><strong class="metric-value">{{ formatNumber(usage?.promptCharacters) }}</strong></ElCard>
      <ElCard shadow="never"><p class="muted">输出字符</p><strong class="metric-value">{{ formatNumber(usage?.outputCharacters) }}</strong></ElCard>
      <ElCard shadow="never"><p class="muted">平均首字延迟</p><strong class="metric-value">{{ usage?.firstTokenMsAverage === null ? '未观测' : formatNumber(usage?.firstTokenMsAverage) }}</strong><span v-if="typeof usage?.firstTokenMsAverage === 'number'" class="muted"> 毫秒</span></ElCard>
    </div>
  </section>
</template>

<style scoped>
.issue-card, .issued-card { margin-top: 20px; }
.issue-form, .usage-metrics { margin-top: 22px; }
.issue-fields { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); gap: 20px; }
.issue-fields :deep(.el-input-number) { width: 100%; }
.issued-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
.issued-code { padding-block: 10px; border-bottom: 1px solid var(--el-border-color-lighter); }
.issued-code:last-child { border-bottom: 0; }
.code-plaintext { margin: 0; padding: 14px 16px; color: var(--el-text-color-primary); background: var(--el-fill-color-light); border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; }
.resource-id { overflow-wrap: anywhere; }
.compact-heading { margin-top: 28px; }
.confirm-section { margin-top: 22px; }
.metric-value { display: inline-block; font-size: 26px; font-weight: 650; line-height: 1.35; color: var(--el-text-color-primary); }
@media (max-width: 800px) { .issue-fields { grid-template-columns: minmax(0, 1fr); gap: 0; } }
</style>

<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue';
import {
  ElAlert, ElButton, ElCard, ElDescriptions, ElDescriptionsItem, ElDialog,
  ElDrawer, ElEmpty, ElInput, ElOption, ElSelect, ElSpace, ElTable, ElTableColumn, ElTag,
} from 'element-plus';
import type { AdminDeviceSession, AdminUser, AdminUserListOptions } from '@allira/client';
import { useAdminView, useAdminQuery, formatDate, formatNumber, yesNo } from '../context';
import DangerConfirm from '../components/DangerConfirm.vue';

type IdentityFilter = NonNullable<AdminUserListOptions['identity']>;

const ctx = useAdminView();
const query = ref('');
const identity = ref<IdentityFilter>('all');
const queryError = ref('');
const hasRequested = ref(false);
const selectedOwner = ref<AdminUser | null>(null);
const drawerOpen = ref(false);
const revokeAllTarget = ref<AdminUser | null>(null);
let filterEpoch = 0;
let ownerEpoch = 0;
let deviceRefreshEpoch = 0;
let ownerAbort = new AbortController();
ctx.signal.addEventListener('abort', () => ownerAbort.abort(), { once: true });

// Keep the active query separate from the editable form so pagination cannot mix filters.
let activeFilter: { q: string; identity: IdentityFilter } = { q: '', identity: 'all' };
const users = useAdminQuery<AdminUser>((cursor, signal) => ctx.client.listUsers({
  ...(activeFilter.q ? { q: activeFilter.q } : {}),
  identity: activeFilter.identity,
  ...(cursor ? { cursor } : {}),
  limit: 30,
  signal,
}));
const { items, loading, error, nextCursor } = users;

// This query does not autoload. A user must explicitly open an owner's drawer first.
const devices = useAdminQuery<AdminDeviceSession>((cursor, signal) => {
  const ownerId = selectedOwner.value?.userId;
  if (!ownerId || !drawerOpen.value) return Promise.resolve({ items: [], nextCursor: null });
  return ctx.client.listUserSessions(ownerId, { ...(cursor ? { cursor } : {}), limit: 30, signal });
});
const {
  items: deviceItems, loading: devicesLoading, error: devicesError, nextCursor: devicesNextCursor,
} = devices;

const knownSessionCount = computed(() => items.value.reduce((total, user) =>
  total + (typeof user.activeSessions === 'number' && Number.isFinite(user.activeSessions) ? user.activeSessions : 0), 0));
const knownOidcCount = computed(() => items.value.filter(user => user.identityStatus === 'oidc').length);
const missingIdentityCount = computed(() => items.value.filter(user => !['oidc', 'legacy', 'developer'].includes(user.identityStatus ?? '')).length);
const writesDisabled = computed(() => ctx.blocked.value || ctx.mutating.value || loading.value || devicesLoading.value);

function optionalText(value: string | null | undefined): string {
  return value === undefined ? '未知' : value === null ? '无' : value;
}
function identityLabel(value: AdminUser['identityStatus']): string {
  return value === 'oidc' ? '已关联开源身份' : value === 'legacy' ? '旧账号 · 待迁移' : value === 'developer' ? '开发身份' : '未知（旧服务未提供）';
}
function identityTone(value: AdminUser['identityStatus']): 'success' | 'warning' | 'info' {
  return value === 'oidc' ? 'success' : value === 'legacy' ? 'warning' : 'info';
}
function accountLabel(value: AdminUser['accountType']): string {
  return value === 'registered' ? '注册账号' : value === 'developer' ? '开发身份' : '未知';
}
function maskedPhone(value: unknown): string {
  // The explicit masked DTO field is the only eligible source; never use any raw phone field.
  return value === null ? '未提供' : typeof value === 'string' && /\*/.test(value) && /^[+\d* -]{4,32}$/.test(value) ? value : '未知';
}
function normalizedQuery(raw: string): string | null {
  // Reject the entire Unicode Other category, including controls, format, private-use and surrogates.
  if (/\p{C}/u.test(raw)) return null;
  const value = raw.trim();
  return Array.from(value).length <= 100 && new TextEncoder().encode(value).length <= 400 ? value : null;
}
function deviceType(value: AdminDeviceSession['type']): string {
  return value === 'browser' ? '浏览器 Cookie' : value === 'bearer' ? '内存 Bearer' : '未知';
}
function localValid(): boolean { return ctx.valid() && !ctx.signal.aborted; }

function closeDevices(): void {
  ++ownerEpoch;
  ++deviceRefreshEpoch;
  ownerAbort.abort();
  ownerAbort = new AbortController();
  devices.reset();
  selectedOwner.value = null;
  drawerOpen.value = false;
  revokeAllTarget.value = null;
}
function updateDrawer(open: boolean): void { if (!open) closeDevices(); }
function updateRevokeDialog(open: boolean): void { if (!open) closeRevokeAll(); }
function invalidateFilters(): void {
  ++filterEpoch;
  users.invalidate();
  closeDevices();
  revokeAllTarget.value = null;
  queryError.value = '';
  hasRequested.value = false;
}
watch([query, identity], invalidateFilters, { flush: 'sync' });

async function refreshUsers(explicit = true): Promise<void> {
  if (!localValid() || ctx.mutating.value) return;
  const normalized = normalizedQuery(query.value);
  ++filterEpoch;
  const own = filterEpoch;
  closeDevices();
  revokeAllTarget.value = null;
  users.reset();
  if (normalized === null) {
    queryError.value = '搜索文字最多 100 个 Unicode 字符、400 UTF-8 字节，不可包含控制、隐藏格式或其他 Unicode C 类字符。';
    hasRequested.value = false;
    return;
  }
  queryError.value = '';
  activeFilter = { q: normalized, identity: identity.value };
  hasRequested.value = true;
  const committed = await users.refresh();
  if (committed && localValid() && own === filterEpoch && explicit) ctx.reconcile();
}
async function loadMoreUsers(): Promise<void> {
  if (!localValid() || ctx.mutating.value) return;
  await users.loadMore();
}
async function openDevices(user: AdminUser): Promise<void> {
  if (!localValid() || ctx.mutating.value || loading.value) return;
  closeDevices();
  selectedOwner.value = user;
  drawerOpen.value = true;
  // Opening is an explicit owner read, but cannot silently unlock an uncertain previous POST.
  await devices.refresh();
}
async function refreshDevices(explicit = true): Promise<void> {
  if (!localValid() || !drawerOpen.value || !selectedOwner.value || ctx.mutating.value) return;
  const ownerId = selectedOwner.value.userId;
  const own = ownerEpoch;
  const request = ++deviceRefreshEpoch;
  await devices.refresh();
  // A device detail read cannot reconcile an uncertain write to another owner.
  void own; void request; void ownerId; void explicit;
}
async function loadMoreDevices(): Promise<void> {
  if (!localValid() || !selectedOwner.value || !drawerOpen.value || ctx.mutating.value) return;
  await devices.loadMore();
}
function openRevokeAll(user: AdminUser): void {
  if (!localValid() || writesDisabled.value) return;
  revokeAllTarget.value = user;
}
function closeRevokeAll(): void { revokeAllTarget.value = null; }

// Supplemental reads have a local filter/owner epoch in addition to the context session guard.
async function refreshOwnerSummary(ownerId: string, filter: number, owner: number): Promise<void> {
  const signal = ownerAbort.signal;
  const current = () => localValid() && !signal.aborted && filter === filterEpoch && owner === ownerEpoch;
  if (!current()) return;
  try {
    const page = await ctx.client.listUsers({ q: ownerId, identity: 'all', limit: 100, signal });
    if (!current()) return;
    const found = page.items.find(user => user.userId === ownerId);
    if (!found) return;
    items.value = items.value.map(user => user.userId === ownerId ? found : user);
    if (selectedOwner.value?.userId === ownerId) selectedOwner.value = found;
  } catch (failure) {
    if (current() && !(failure instanceof Error && failure.name === 'AbortError')) ctx.fail(failure);
  }
}
async function revokeDevice(device: AdminDeviceSession): Promise<void> {
  const ownerId = selectedOwner.value?.userId;
  if (!ownerId || !drawerOpen.value || !localValid() || writesDisabled.value || !deviceItems.value.some(item => item.id === device.id)) return;
  const owner = ownerEpoch;
  const filter = filterEpoch;
  await ctx.mutate(
    () => ctx.client.revokeUserSession(ownerId, device.id, { signal: ctx.signal }),
    async result => {
      if (!localValid() || owner !== ownerEpoch || filter !== filterEpoch || selectedOwner.value?.userId !== ownerId) return;
      ctx.notify(`已撤销 ${formatNumber(result.revoked)} 个本地登录。不是封禁或全局 SSO 注销。`, 'success');
      await refreshOwnerSummary(ownerId, filter, owner);
      if (localValid() && owner === ownerEpoch && filter === filterEpoch && drawerOpen.value && selectedOwner.value?.userId === ownerId) await devices.refresh();
    },
  );
}
async function revokeAll(): Promise<void> {
  const target = revokeAllTarget.value;
  if (!target || !localValid() || writesDisabled.value || !items.value.some(user => user.userId === target.userId)) return;
  const owner = ownerEpoch;
  const filter = filterEpoch;
  // Consume the dialog before awaiting. Any retry needs a new dialog and a new checkbox confirmation.
  revokeAllTarget.value = null;
  await ctx.mutate(
    () => ctx.client.revokeUserSessions(target.userId, { signal: ctx.signal }),
    async result => {
      if (!localValid() || filter !== filterEpoch || owner !== ownerEpoch) return;
      ctx.notify(`已撤销 ${formatNumber(result.revoked)} 个用户登录（仅 AlliraAi 本地）。不是封禁、删除账号或全局 SSO 注销。`, 'success');
      await refreshOwnerSummary(target.userId, filter, owner);
      if (localValid() && owner === ownerEpoch && filter === filterEpoch && drawerOpen.value && selectedOwner.value?.userId === target.userId) await devices.refresh();
    },
  );
}

watch(() => ctx.blocked.value, blocked => { if (blocked) revokeAllTarget.value = null; });
onScopeDispose(() => {
  ++filterEpoch;
  ++ownerEpoch;
  ownerAbort.abort();
  users.reset();
  devices.reset();
  query.value = '';
  selectedOwner.value = null;
  revokeAllTarget.value = null;
});
void refreshUsers(false);
</script>

<template>
  <section aria-labelledby="users-heading">
    <header class="page-heading">
      <div>
        <p class="section-subtitle">账号与访问 / 用户目录</p>
        <h1 id="users-heading">用户管理</h1>
        <p class="muted">查看业务账号与脱敏身份摘要，按需读取本地设备登录。</p>
      </div>
      <ElButton :loading="loading" :disabled="ctx.mutating.value" @click="refreshUsers(true)">刷新用户列表</ElButton>
    </header>

    <div class="metric-grid" aria-label="当前已加载用户摘要">
      <ElCard shadow="never"><p class="section-subtitle">已加载用户</p><h2>{{ formatNumber(items.length) }}</h2><p class="muted">当前筛选，不代表全站总量</p></ElCard>
      <ElCard shadow="never"><p class="section-subtitle">已关联开源身份</p><h2>{{ formatNumber(knownOidcCount) }}</h2><p class="muted">仅统计本页已知身份</p></ElCard>
      <ElCard shadow="never"><p class="section-subtitle">已知有效本地登录</p><h2>{{ formatNumber(knownSessionCount) }}</h2><p class="muted">当前已加载用户的已知字段合计</p></ElCard>
      <ElCard shadow="never"><p class="section-subtitle">身份未知</p><h2>{{ formatNumber(missingIdentityCount) }}</h2><p class="muted">缺失字段不推断为正常或开发身份</p></ElCard>
    </div>

    <ElCard class="data-card" shadow="never">
      <template #header>
        <div><h2>用户目录</h2><p id="user-query-help" class="section-subtitle">按用户 ID、用户名或显示名称字面搜索；不搜索手机原文。</p></div>
      </template>
      <form class="toolbar" aria-label="用户筛选" @submit.prevent="refreshUsers(true)">
        <ElInput v-model="query" class="query-input" :disabled="ctx.mutating.value" aria-label="搜索用户" aria-describedby="user-query-help" placeholder="用户 ID、用户名或显示名称" :maxlength="400" clearable />
        <ElSelect v-model="identity" class="filter-select" :disabled="ctx.mutating.value" aria-label="身份类型">
          <ElOption label="全部身份" value="all" />
          <ElOption label="已关联开源身份" value="oidc" />
          <ElOption label="旧账号 · 待迁移" value="legacy" />
          <ElOption label="开发身份" value="developer" />
        </ElSelect>
        <ElButton native-type="submit" type="primary" :loading="loading" :disabled="ctx.mutating.value">筛选用户</ElButton>
      </form>
      <ElAlert v-if="queryError" :title="queryError" type="warning" :closable="false" show-icon />
      <ElAlert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <ElAlert v-if="ctx.blocked.value" title="上一项写入结果不确定，危险操作已锁定。请显式刷新对应用户或设备列表，读取核对后再重新确认；系统不会自动重试。" type="warning" :closable="false" show-icon />
      <ElTable :data="items" row-key="userId" :aria-busy="loading" empty-text="暂无用户" :scrollbar-always-on="true">
        <!-- @vue-generic {AdminUser} -->
        <ElTableColumn label="用户 / 账号" min-width="220">
          <template #default="{ row }"><strong>{{ optionalText(row.displayName) }}</strong><p class="mono">{{ row.userId }}</p><p class="muted">用户名：{{ optionalText(row.username) }}</p><ElTag type="info" size="small" effect="plain">{{ accountLabel(row.accountType) }}</ElTag></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminUser} -->
        <ElTableColumn label="身份摘要" min-width="210">
          <template #default="{ row }"><ElTag :type="identityTone(row.identityStatus)" size="small" effect="light">{{ identityLabel(row.identityStatus) }}</ElTag><p class="mono">{{ maskedPhone(row.maskedPhone) }}</p><p class="muted">手机已验证：{{ yesNo(row.phoneVerified) }}</p><p class="muted">历史密码已设置：{{ yesNo(row.passwordEnabled) }}</p></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminUser} -->
        <ElTableColumn label="能量余额" min-width="105"><template #default="{ row }">{{ row.walletBalance === null ? '未设置' : formatNumber(row.walletBalance) }}</template></ElTableColumn>
        <!-- @vue-generic {AdminUser} -->
        <ElTableColumn label="创建时间" min-width="175"><template #default="{ row }">{{ formatDate(row.createdAt) }}</template></ElTableColumn>
        <!-- @vue-generic {AdminUser} -->
        <ElTableColumn label="业务会话 / 任务" min-width="130"><template #default="{ row }">{{ formatNumber(row.conversationCount) }} / {{ formatNumber(row.runCount) }}</template></ElTableColumn>
        <!-- @vue-generic {AdminUser} -->
        <ElTableColumn label="有效本地登录" min-width="120"><template #default="{ row }">{{ formatNumber(row.activeSessions) }}</template></ElTableColumn>
        <!-- @vue-generic {AdminUser} -->
        <ElTableColumn label="操作" min-width="185" fixed="right">
          <template #default="{ row }"><ElSpace direction="vertical" alignment="start"><ElButton size="small" :disabled="ctx.mutating.value || loading" @click="openDevices(row)">查看设备</ElButton><ElButton size="small" type="danger" plain :disabled="writesDisabled" @click="openRevokeAll(row)">撤销全部本地登录</ElButton></ElSpace></template>
        </ElTableColumn>
        <template #empty><ElEmpty :description="loading ? '正在读取用户…' : hasRequested ? '当前筛选暂无用户' : '筛选已变更，请点击“筛选用户”'" /></template>
      </ElTable>
      <footer class="table-footer"><span class="muted" role="status">已显示 {{ formatNumber(items.length) }} 条用户记录<span v-if="nextCursor">，还有下一页</span></span><ElButton v-if="nextCursor" :loading="loading" :disabled="ctx.mutating.value" @click="loadMoreUsers">加载更多用户</ElButton></footer>
    </ElCard>

    <ElAlert title="操作边界" type="info" :closable="false" show-icon>
      撤销仅影响 AlliraAi 本地账号 / 开发登录及相应待处理证明，不是封禁、删除账号或 Logto 全局 SSO 注销。用户仍可经认证重新登录，开发身份仍受服务端开发门禁约束。Logto Console 属于独立身份管理，普通登录不授予后台权限。
    </ElAlert>

    <ElDrawer :model-value="drawerOpen" title="用户设备会话" size="min(960px, 100vw)" destroy-on-close :close-on-click-modal="!ctx.mutating.value" :close-on-press-escape="!ctx.mutating.value" :show-close="!ctx.mutating.value" @update:model-value="updateDrawer">
      <template v-if="selectedOwner">
        <ElDescriptions :column="1" border title="当前业务账号">
          <ElDescriptionsItem label="用户 ID"><span class="mono">{{ selectedOwner.userId }}</span></ElDescriptionsItem>
          <ElDescriptionsItem label="显示名称">{{ optionalText(selectedOwner.displayName) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="脱敏手机号">{{ maskedPhone(selectedOwner.maskedPhone) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="身份">{{ identityLabel(selectedOwner.identityStatus) }}</ElDescriptionsItem>
        </ElDescriptions>
        <p class="section-subtitle">仅在点击“查看设备”后读取，列表仅含仍有效的 AlliraAi 本地登录。不返回或展示 Token；设备会话 ID 仅用于撤销请求。</p>
        <div class="toolbar"><ElButton :loading="devicesLoading" :disabled="ctx.mutating.value" @click="refreshDevices(true)">刷新设备列表</ElButton><ElButton type="danger" plain :disabled="writesDisabled" @click="openRevokeAll(selectedOwner)">撤销此用户全部本地登录</ElButton></div>
        <ElAlert v-if="devicesError" :title="devicesError" type="error" :closable="false" show-icon />
        <ElAlert v-if="ctx.blocked.value" title="写入结果不确定；请显式刷新本设备列表核对，再重新确认。不会自动重试撤销。" type="warning" :closable="false" show-icon />
        <ElTable :data="deviceItems" row-key="id" :aria-busy="devicesLoading" :scrollbar-always-on="true">
          <!-- @vue-generic {AdminDeviceSession} -->
          <ElTableColumn prop="label" label="设备标签" min-width="160" />
          <!-- @vue-generic {AdminDeviceSession} -->
          <ElTableColumn label="本地会话类型" min-width="140"><template #default="{ row }">{{ deviceType(row.type) }}</template></ElTableColumn>
          <!-- @vue-generic {AdminDeviceSession} -->
          <ElTableColumn label="创建 / 最近活动 / 到期" min-width="190"><template #default="{ row }"><p>{{ formatDate(row.createdAt) }}</p><p class="muted">{{ formatDate(row.lastSeenAt) }}</p><p class="muted">{{ formatDate(row.expiresAt) }}</p></template></ElTableColumn>
          <!-- @vue-generic {AdminDeviceSession} -->
          <ElTableColumn label="操作" min-width="210"><template #default="{ row }"><DangerConfirm :key="`${ownerEpoch}:${row.id}`" title="撤销此设备登录" description="仅撤销此设备的 AlliraAi 本地登录；不删除账号和数据，不是封禁，也不会退出 Logto 全局 SSO。" confirm-label="确认撤销此设备" :disabled="writesDisabled" :busy="ctx.mutating.value" @confirm="revokeDevice(row)" /></template></ElTableColumn>
          <template #empty><ElEmpty :description="devicesLoading ? '正在读取设备…' : '当前没有有效本地设备登录'" /></template>
        </ElTable>
        <footer class="table-footer"><span class="muted" role="status">已显示 {{ formatNumber(deviceItems.length) }} 个有效本地设备</span><ElButton v-if="devicesNextCursor" :loading="devicesLoading" :disabled="ctx.mutating.value" @click="loadMoreDevices">加载更多设备</ElButton></footer>
        <ElAlert title="撤销不阻止重新登录" description="此操作不会修改上游身份、账号资料、聊天或能量。设备标签不能作为真实设备或操作者身份的证明。" type="info" :closable="false" />
      </template>
    </ElDrawer>

    <ElDialog :model-value="revokeAllTarget !== null" title="撤销用户全部本地登录" width="min(560px, 94vw)" destroy-on-close @update:model-value="updateRevokeDialog">
      <template v-if="revokeAllTarget"><p class="mono">{{ revokeAllTarget.userId }}</p><DangerConfirm :key="`${filterEpoch}:${revokeAllTarget.userId}`" title="确认撤销此用户当前全部本地登录" description="仅撤销 AlliraAi 当前本地账号 / 开发登录及相应待处理证明。不删除账号、聊天或能量，不是封禁或 Logto 全局 SSO 注销；用户仍可经认证重新登录。" confirm-label="确认撤销全部本地登录" :disabled="writesDisabled" :busy="ctx.mutating.value" @confirm="revokeAll" /></template>
    </ElDialog>
  </section>
</template>

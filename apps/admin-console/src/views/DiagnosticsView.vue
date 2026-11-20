<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue';
import { ElAlert, ElButton, ElCard, ElDescriptions, ElDescriptionsItem, ElEmpty, ElTag } from 'element-plus';
import type { AdminDiagnostics } from '@allira/client';
import { useAdminView, formatDate, formatNumber, yesNo } from '../context';

const ctx = useAdminView();
const value = ref<AdminDiagnostics | null>(null);
const loading = ref(false);
const readFailed = ref(false);
let requestEpoch = 0;
const identity = computed(() => value.value?.identity);
const identityBackup = computed(() => value.value?.identityBackup);
const warningCount = computed(() => {
  const warnings: unknown = value.value?.warnings;
  return Array.isArray(warnings) ? warnings.length : undefined;
});

function backupLabel(status: unknown): string {
  return status === 'missing' ? '缺失' : status === 'recent' ? '近期' : status === 'stale' ? '陈旧' : status === 'invalid' ? '无效' : '未知';
}
function backupTone(status: unknown): 'success' | 'warning' | 'danger' | 'info' {
  return status === 'recent' ? 'info' : status === 'stale' ? 'warning' : status === 'invalid' || status === 'missing' ? 'danger' : 'info';
}
function databaseLabel(ok: unknown): string {
  return ok === true ? '本地检查正常' : ok === false ? '本地检查异常' : '未知';
}
function journalLabel(mode: unknown): string {
  return typeof mode === 'string' && ['delete', 'truncate', 'persist', 'memory', 'wal', 'off'].includes(mode.toLowerCase()) ? mode.toUpperCase() : '未知';
}
function backendLabel(backend: unknown): string {
  return backend === 'oidc' ? 'OIDC / Logto' : backend === 'legacy' ? '旧兼容后端' : '未知';
}
function gateLabel(enabled: unknown): string {
  return enabled === true ? '已启用（不代表真实渠道已验收）' : enabled === false ? '已关闭' : '未知';
}
function restoreLabel(backup: NonNullable<AdminDiagnostics['identityBackup']>): string {
  return backup.restoreStatus === 'recorded' ? '仅有历史报告（recorded），本次未验证恢复' : backup.restoreStatus === 'not_reported' ? '未提供恢复报告（not_reported）' : backup.restoreStatus === 'invalid' ? '历史报告无效（invalid）' : '未知';
}
async function refresh(): Promise<void> {
  if (!ctx.valid() || ctx.signal.aborted || loading.value) return;
  const request = ++requestEpoch;
  loading.value = true;
  readFailed.value = false;
  value.value = null;
  try {
    const result = await ctx.client.getDiagnostics({ signal: ctx.signal });
    if (!ctx.valid() || ctx.signal.aborted || request !== requestEpoch) return;
    value.value = result;
    // Metadata is not evidence about an uncertain business mutation, so it does not reconcile it.
  } catch (failure) {
    if (ctx.valid() && !ctx.signal.aborted && request === requestEpoch && !(failure instanceof Error && failure.name === 'AbortError')) {
      readFailed.value = true;
      ctx.fail(failure);
    }
  } finally {
    if (ctx.valid() && !ctx.signal.aborted && request === requestEpoch) loading.value = false;
  }
}
onScopeDispose(() => { ++requestEpoch; value.value = null; });
void refresh();
</script>

<template>
  <section aria-labelledby="diagnostics-heading">
    <header class="page-heading">
      <div><p class="section-subtitle">系统管理 / 运行元数据</p><h1 id="diagnostics-heading">健康诊断</h1><p class="muted">分开查看业务 SQLite、身份 PostgreSQL 与认证运行门禁。</p></div>
      <ElButton :loading="loading" @click="refresh">刷新健康诊断</ElButton>
    </header>

    <ElAlert title="这是脱敏元数据，不是端到端健康验收" description="本页不展示路径、issuer / sub、手机原文、凭据或聊天内容，不直接调用 Logto Management，不执行身份服务或模型探测。" type="info" :closable="false" show-icon />
    <ElAlert v-if="readFailed" title="健康诊断读取失败，请手动刷新；不会自动重试。" type="error" :closable="false" show-icon />

    <template v-if="value">
      <div class="metric-grid" aria-label="诊断摘要">
        <ElCard shadow="never"><p class="section-subtitle">业务 SQLite</p><h2>{{ databaseLabel(value.database.ok) }}</h2><p class="muted">结构版本 {{ formatNumber(value.schemaVersion) }}</p></ElCard>
        <ElCard shadow="never"><p class="section-subtitle">业务备份元数据</p><h2>{{ backupLabel(value.backup.status) }}</h2><p class="muted">近期不等于已验证可恢复</p></ElCard>
        <ElCard shadow="never"><p class="section-subtitle">身份服务探测</p><h2>{{ identity?.serviceProbe === 'not_performed' ? '未执行' : '未知' }}</h2><p class="muted">运行门禁不证明真实身份渠道可用</p></ElCard>
        <ElCard shadow="never"><p class="section-subtitle">运行提醒</p><h2>{{ formatNumber(warningCount) }}</h2><p class="muted">仅显示数量，不回显原始内容</p></ElCard>
      </div>

      <ElCard class="data-card" shadow="never">
        <template #header><div><h2>业务数据库 · SQLite</h2><p class="section-subtitle">本应用业务库，与 Logto 身份库独立备份。</p></div></template>
        <ElDescriptions :column="1" border>
          <ElDescriptionsItem label="服务版本">{{ value.version }}</ElDescriptionsItem>
          <ElDescriptionsItem label="启动时间">{{ formatDate(value.startedAt) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="运行秒数">{{ formatNumber(value.uptimeSeconds) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="数据库结构版本">{{ formatNumber(value.schemaVersion) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="数据库检查"><ElTag :type="value.database.ok === true ? 'success' : value.database.ok === false ? 'danger' : 'info'" effect="plain">{{ databaseLabel(value.database.ok) }}</ElTag></ElDescriptionsItem>
          <ElDescriptionsItem label="日志模式"><span class="mono">{{ journalLabel(value.database.journalMode) }}</span></ElDescriptionsItem>
          <ElDescriptionsItem label="数据库字节数">{{ formatNumber(value.database.databaseBytes) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="WAL 字节数">{{ formatNumber(value.database.walBytes) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="已配置业务备份">{{ yesNo(value.backup.configured) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="最近业务备份">{{ formatDate(value.backup.latestAt) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="业务备份秒龄">{{ formatNumber(value.backup.ageSeconds) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="业务备份状态"><ElTag :type="backupTone(value.backup.status)" effect="plain">{{ backupLabel(value.backup.status) }}</ElTag></ElDescriptionsItem>
        </ElDescriptions>
      </ElCard>

      <ElCard class="data-card" shadow="never">
        <template #header><div><h2>运行与本地会话</h2><p class="section-subtitle">计数为读取时快照，不代表真实负载测试或生产容量。</p></div></template>
        <ElDescriptions :column="1" border>
          <ElDescriptionsItem label="生成中">{{ formatNumber(value.runs.running) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="运行中处理">{{ formatNumber(value.runs.inFlight) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="关闭中">{{ formatNumber(value.runs.closing) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="并发容量配置">{{ formatNumber(value.runs.capacity) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="Bearer 会话">{{ formatNumber(value.sessions.bearer) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="浏览器会话">{{ formatNumber(value.sessions.browser) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="管理会话">{{ formatNumber(value.sessions.admin) }}</ElDescriptionsItem>
        </ElDescriptions>
      </ElCard>

      <ElCard class="data-card" shadow="never">
        <template #header><div><h2>认证运行门禁</h2><p class="section-subtitle">AlliraAi 门禁与上游身份服务可用性是不同事实。</p></div></template>
        <ElDescriptions v-if="identity" :column="1" border>
          <ElDescriptionsItem label="认证后端">{{ backendLabel(identity.backend) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="已配置">{{ yesNo(identity.configured) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="运行门禁">{{ gateLabel(identity.enabled) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="模拟渠道标志">{{ yesNo(identity.simulated) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="已关联账号">{{ formatNumber(identity.linkedAccounts) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="旧账号">{{ formatNumber(identity.legacyAccounts) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="开发身份">{{ formatNumber(identity.developerUsers) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="待处理过渡流程">{{ formatNumber(identity.pendingFlows) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="身份服务探测"><ElTag type="info" effect="plain">{{ identity.serviceProbe === 'not_performed' ? '未执行（not_performed）' : '未知' }}</ElTag></ElDescriptionsItem>
          <ElDescriptionsItem label="身份管理">{{ identity.administration === 'separate' ? '独立 Logto Console，不授予普通登录者管理权限' : '未知' }}</ElDescriptionsItem>
        </ElDescriptions>
        <ElAlert v-else title="身份诊断未知" description="旧服务未提供 identity；不推断门禁、关联人数或身份服务正常。" type="warning" :closable="false" show-icon />
      </ElCard>

      <ElCard class="data-card" shadow="never">
        <template #header><div><h2>身份数据库 · Logto PostgreSQL</h2><p class="section-subtitle">独立身份备份边界，不与业务 SQLite 的状态互相替代。</p></div></template>
        <template v-if="identityBackup">
          <ElDescriptions :column="1" border>
            <ElDescriptionsItem label="已配置身份备份">{{ yesNo(identityBackup.configured) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="最近身份备份">{{ formatDate(identityBackup.latestAt) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="身份备份秒龄">{{ formatNumber(identityBackup.ageSeconds) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="身份备份状态"><ElTag :type="backupTone(identityBackup.status)" effect="plain">{{ backupLabel(identityBackup.status) }}</ElTag></ElDescriptionsItem>
            <ElDescriptionsItem label="身份数据库字节数">{{ formatNumber(identityBackup.databaseBytes) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="恢复报告">{{ restoreLabel(identityBackup) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="历史报告时间">{{ formatDate(identityBackup.restoreVerifiedAt) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="本次校验和验证">{{ identityBackup.checksumVerified === false ? '未验证（checksumVerified=false）' : '未知：未证明本次校验' }}</ElDescriptionsItem>
          </ElDescriptions>
          <ElAlert title="历史报告 ≠ 当前验证" description="recorded 仅表示已有历史恢复报告，不代表本次执行了恢复演练或 checksum 校验。“近期备份”同样不证明本次可恢复。" type="warning" :closable="false" show-icon />
        </template>
        <ElAlert v-else title="身份备份未知" description="旧服务未提供 identityBackup；不推断已备份、已校验或已验证恢复。" type="warning" :closable="false" show-icon />
      </ElCard>

      <ElCard class="data-card" shadow="never">
        <template #header><div><h2>运行提醒</h2><p class="section-subtitle">仅展示服务返回的提醒数量，敏感原文不进入页面。</p></div></template>
        <ElAlert v-if="typeof warningCount === 'number' && warningCount > 0" :title="`服务报告 ${formatNumber(warningCount)} 项提醒`" description="请由运维在受保护环境中安全核对。本页不回显可能包含敏感信息的原始诊断文本。" type="warning" :closable="false" show-icon />
        <ElAlert v-else-if="warningCount === 0" title="服务未报告额外提醒" description="这不是身份服务、模型服务或端到端健康探测通过的证明。" type="info" :closable="false" show-icon />
        <ElAlert v-else title="提醒数量未知" description="不推断当前没有运行提醒。" type="info" :closable="false" show-icon />
      </ElCard>
      <ElAlert title="备份与数据安全边界" description="业务 SQLite 与身份 PostgreSQL 是独立备份边界。本页不承诺全局身份生命周期、物理擦除或异机灾备；备份元数据不等于当前校验或恢复演练。" type="info" :closable="false" show-icon />
    </template>
    <ElCard v-else class="data-card" shadow="never"><ElEmpty :description="loading ? '正在读取诊断元数据…' : '暂无诊断数据，请显式刷新'" /></ElCard>
  </section>
</template>

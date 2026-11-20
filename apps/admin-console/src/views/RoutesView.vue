<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue';
import { ElAlert, ElButton, ElCard, ElDescriptions, ElDescriptionsItem, ElEmpty, ElTable, ElTableColumn, ElTag } from 'element-plus';
import type { AdminRouteOverview, ModelRoute } from '@allira/client';
import { useAdminView, formatNumber, yesNo } from '../context';

const ctx = useAdminView();
const value = ref<AdminRouteOverview | null>(null);
const loading = ref(false);
const readFailed = ref(false);
let requestEpoch = 0;
const configuredCount = computed(() => value.value?.items.filter(route => route.configured === true).length ?? 0);
const limits = computed(() => value.value ? [
  { label: '生成超时', value: `${formatNumber(value.value.limits.runTimeoutSeconds)} 秒` },
  { label: '最大并发', value: formatNumber(value.value.limits.maxConcurrentRuns) },
  { label: '输入字符上限', value: formatNumber(value.value.limits.maxPromptCharacters) },
  { label: '输出字符上限', value: formatNumber(value.value.limits.maxOutputCharacters) },
  { label: '上游 max_tokens 配置', value: formatNumber(value.value.limits.upstreamMaxTokens) },
  { label: '每分钟生成频率配置', value: formatNumber(value.value.limits.runRatePerMinute) },
] : []);

function modeLabel(mode: ModelRoute['mode']): string {
  return mode === 'economy' ? '经济模式' : mode === 'fast' ? '极速模式' : '未知';
}
function providerLabel(provider: ModelRoute['provider']): string {
  return provider === 'mock' ? 'Mock 模拟' : provider === 'openai' ? 'OpenAI 兼容' : '未知';
}
function fallbackLabel(mode: ModelRoute['fallbackTo']): string {
  return mode === 'economy' ? '经济模式' : mode === null ? '无' : '未知';
}
function configuredTone(configured: unknown): 'success' | 'warning' | 'info' {
  return configured === true ? 'success' : configured === false ? 'warning' : 'info';
}
async function refresh(): Promise<void> {
  if (!ctx.valid() || ctx.signal.aborted || loading.value) return;
  const request = ++requestEpoch;
  loading.value = true;
  readFailed.value = false;
  value.value = null;
  try {
    const result = await ctx.client.getRoutes({ signal: ctx.signal });
    if (!ctx.valid() || ctx.signal.aborted || request !== requestEpoch) return;
    value.value = result;
    // Configuration reads cannot reconcile an unrelated, uncertain business POST.
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
  <section aria-labelledby="routes-heading">
    <header class="page-heading">
      <div><p class="section-subtitle">系统管理 / 模型路由</p><h1 id="routes-heading">路由配置</h1><p class="muted">只读查看模式、能量与限额配置，不发送模型请求。</p></div>
      <ElButton :loading="loading" @click="refresh">刷新路由配置</ElButton>
    </header>

    <ElAlert title="配置不等于可用性" description="本页不展示或编辑 Key、上游地址、模型秘密配置，不调用模型。已配置不代表真实渠道、额度、报价或生产容量已验证。" type="info" :closable="false" show-icon />
    <ElAlert v-if="readFailed" title="路由配置读取失败，请手动刷新；不会自动重试。" type="error" :closable="false" show-icon />

    <template v-if="value">
      <div class="metric-grid" aria-label="路由配置摘要">
        <ElCard shadow="never"><p class="section-subtitle">路由模式</p><h2>{{ formatNumber(value.items.length) }}</h2><p class="muted">服务端返回的配置项</p></ElCard>
        <ElCard shadow="never"><p class="section-subtitle">已配置模式</p><h2>{{ formatNumber(configuredCount) }}</h2><p class="muted">只代表 configured 字段为真</p></ElCard>
        <ElCard shadow="never"><p class="section-subtitle">并发配置</p><h2>{{ formatNumber(value.limits.maxConcurrentRuns) }}</h2><p class="muted">不是实测吞吐或容量</p></ElCard>
        <ElCard shadow="never"><p class="section-subtitle">本页探测</p><h2>不执行</h2><p class="muted">仅配置 / 无模型请求</p></ElCard>
      </div>

      <ElCard class="data-card" shadow="never">
        <template #header><div><h2>模式与回退</h2><p class="section-subtitle">配置摘要 · 不包含上游地址或凭据</p></div></template>
        <ElTable :data="value.items" row-key="mode" :aria-busy="loading" :scrollbar-always-on="true" empty-text="未提供路由模式">
          <ElTableColumn label="模式" min-width="160"><template #default="{ row }"><strong>{{ modeLabel(row.mode) }}</strong></template></ElTableColumn>
          <ElTableColumn label="供应商类型" min-width="180"><template #default="{ row }"><ElTag :type="row.provider === 'mock' ? 'info' : 'primary'" effect="plain">{{ providerLabel(row.provider) }}</ElTag></template></ElTableColumn>
          <ElTableColumn label="已配置" min-width="125"><template #default="{ row }"><ElTag :type="configuredTone(row.configured)" effect="light">{{ yesNo(row.configured) }}</ElTag></template></ElTableColumn>
          <ElTableColumn label="每次能量" min-width="130"><template #default="{ row }">{{ formatNumber(row.energyCost) }}</template></ElTableColumn>
          <ElTableColumn label="回退模式" min-width="150"><template #default="{ row }">{{ fallbackLabel(row.fallbackTo) }}</template></ElTableColumn>
        </ElTable>
        <footer class="table-footer"><span class="muted">能量是应用内配置，不等于上游货币报价。</span><ElTag type="info" effect="plain">{{ value.configurationOnly === true ? 'configurationOnly = true' : '配置标志未知' }}</ElTag></footer>
      </ElCard>

      <ElCard class="data-card" shadow="never">
        <template #header><div><h2>运行限制</h2><p class="section-subtitle">输入 / 输出为字符配置，max_tokens 为独立上游参数。</p></div></template>
        <ElDescriptions :column="1" border><ElDescriptionsItem v-for="limit in limits" :key="limit.label" :label="limit.label">{{ limit.value }}</ElDescriptionsItem></ElDescriptions>
      </ElCard>
      <ElAlert v-if="value.configurationOnly !== true" title="服务端配置标志未知；不推断模型已通过探测，本页仍不执行模型请求。" type="warning" :closable="false" show-icon />
    </template>
    <ElCard v-else class="data-card" shadow="never"><ElEmpty :description="loading ? '正在读取路由配置…' : '暂无路由配置，请显式刷新'" /></ElCard>
  </section>
</template>

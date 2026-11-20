<script setup lang="ts">
import { computed, onMounted, shallowRef, ref } from 'vue';
import { ElAlert, ElButton, ElCard, ElTag } from 'element-plus';
import { User, ChatDotRound, Document, Cpu, Right, Refresh } from '@element-plus/icons-vue';
import type { AdminOverview } from '@allira/client';
import { useAdminView, formatNumber } from '../context';
const ctx = useAdminView();
const data = shallowRef<AdminOverview | null>(null), loading = ref(false);
let version = 0;
const stats = computed(() => [
  { label: '用户总数', value: data.value?.users, icon: User, note: '业务账号与开发身份', color: 'blue' },
  { label: '会话总数', value: data.value?.conversations, icon: ChatDotRound, note: '按所有者隔离保存', color: 'purple' },
  { label: '持久化消息', value: data.value?.messages, icon: Document, note: '完整对话与原始记录', color: 'teal' },
  { label: '当前生成中', value: data.value?.runs.running, icon: Cpu, note: `最大并发 ${formatNumber(data.value?.maxConcurrentRuns)}`, color: 'orange' },
]);
const total = computed(() => data.value ? Object.values(data.value.runs).reduce((sum, value) => sum + value, 0) : 0);
const states = computed(() => [
  { label: '已完成', value: data.value?.runs.completed, color: 'teal' },
  { label: '生成中', value: data.value?.runs.running, color: 'blue' },
  { label: '已停止', value: data.value?.runs.cancelled, color: 'orange' },
  { label: '失败', value: data.value?.runs.failed, color: 'red' },
]);
async function refresh(): Promise<void> {
  const captured = ++version; loading.value = true;
  try { const result = await ctx.client.overview({ signal: ctx.signal }); if (ctx.valid() && captured === version) { data.value = result; } }
  catch (error) { if (ctx.valid() && captured === version) ctx.fail(error); }
  finally { if (ctx.valid() && captured === version) loading.value = false; }
}
onMounted(refresh);
</script>
<template>
  <section class="admin-page overview-page">
    <div class="page-heading"><div><h1>概览</h1><p class="section-subtitle">欢迎回来。这里是 AlliraAi 的运行与业务快照。</p></div><ElButton :loading="loading" @click="refresh"><Refresh />刷新概览</ElButton></div>
    <div class="metric-grid">
      <ElCard v-for="stat in stats" :key="stat.label" shadow="never" class="metric-card"><div class="metric-top"><span>{{ stat.label }}</span><span class="metric-icon" :class="stat.color"><component :is="stat.icon" /></span></div><strong class="metric-value">{{ formatNumber(stat.value) }}</strong><p>{{ stat.note }}</p></ElCard>
    </div>
    <div class="overview-grid">
      <ElCard shadow="never" class="data-card"><template #header><div class="card-heading"><div><h2>任务状态分布</h2><p>已持久化任务 · 不是实时容量测试</p></div><ElTag effect="plain" type="info">合计 {{ formatNumber(total) }}</ElTag></div></template><div class="run-state-list"><div v-for="item in states" :key="item.label" class="run-state-row"><span><i class="state-dot" :class="item.color"></i>{{ item.label }}</span><progress class="run-bar-native" :class="item.color" :max="total || 1" :value="item.value ?? 0" :aria-label="`${item.label}任务占比`"></progress><strong>{{ formatNumber(item.value) }}</strong></div></div><div class="empty-chart-note" v-if="total === 0">暂无任务记录，数据将在实际使用后出现。</div></ElCard>
      <ElCard shadow="never" class="data-card"><template #header><div class="card-heading"><div><h2>工作空间状态</h2><p>当前服务提供的配置摘要</p></div><span class="status-dot"></span></div></template><dl class="status-list"><div><dt>运行环境</dt><dd><ElTag type="info" effect="light">{{ data?.environment ?? '未知' }}</ElTag></dd></div><div><dt>模型适配类型</dt><dd>{{ data?.provider === 'openai' ? 'OpenAI 兼容' : data?.provider === 'mock' ? 'Mock' : '未知' }}</dd></div><div><dt>有效用户登录</dt><dd>{{ formatNumber(data?.activeSessions) }}</dd></div><div><dt>管理认证</dt><dd><span class="inline-protected">Cloudflare Access</span></dd></div></dl></ElCard>
    </div>
    <ElCard shadow="never" class="security-strip"><div class="security-strip-icon"><Right /></div><div><h2>把安全边界留在每一次操作里</h2><p>聊天正文只在显式查看时读取；危险操作需再次确认。用户登录撤销仅影响本应用，不是 Logto 全局 SSO 注销。</p></div><ElTag type="success" effect="plain">独立管理权限</ElTag></ElCard>
    <ElAlert title="概览是业务数据与配置快照，不调用模型、不探测身份服务，也不证明真实短信、腾讯渠道或生产容量。" type="info" :closable="false" />
  </section>
</template>

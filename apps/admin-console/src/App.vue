<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElAlert, ElButton, ElConfigProvider, ElTag } from 'element-plus';
import { DataAnalysis, User, ChatLineRound, Operation, Ticket, Coin, Connection, Monitor, Document, SwitchButton, Menu, Lock, ArrowRight, Refresh } from '@element-plus/icons-vue';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import { provideAdmin, formatDate } from './context';
import type { AdminView } from './context';
import OverviewView from './views/OverviewView.vue';
import UsersView from './views/UsersView.vue';
import ConversationsView from './views/ConversationsView.vue';
import RunsView from './views/RunsView.vue';
import EconomyView from './views/EconomyView.vue';
import RoutesView from './views/RoutesView.vue';
import DiagnosticsView from './views/DiagnosticsView.vue';
import AuditView from './views/AuditView.vue';
const state = provideAdmin();
const { phase, session, epoch, view, error, notification, blocked } = state;
const mobileOpen = ref(false);
const tabs = [
  { key: 'overview' as const, label: '概览', caption: '运行总览', icon: DataAnalysis, group: '工作空间' },
  { key: 'users' as const, label: '用户', caption: '身份与设备', icon: User, group: '' },
  { key: 'conversations' as const, label: '会话', caption: '内容与归属', icon: ChatLineRound, group: '' },
  { key: 'runs' as const, label: '生成任务', caption: '任务与状态', icon: Operation, group: '' },
  { key: 'cdks' as const, label: '兑换码', caption: '能量发放', icon: Ticket, group: '平台运营' },
  { key: 'usage' as const, label: '能量用量', caption: '使用统计', icon: Coin, group: '' },
  { key: 'routes' as const, label: '路由配置', caption: '只读配置', icon: Connection, group: '系统管理' },
  { key: 'diagnostics' as const, label: '健康诊断', caption: '门禁与备份', icon: Monitor, group: '' },
  { key: 'audit' as const, label: '审计', caption: '管理操作', icon: Document, group: '' },
];
const current = computed(() => tabs.find(item => item.key === view.value)!);
const component = computed(() => ({ overview: OverviewView, users: UsersView, conversations: ConversationsView, runs: RunsView, cdks: EconomyView, usage: EconomyView, routes: RoutesView, diagnostics: DiagnosticsView, audit: AuditView })[view.value]);
const identity = computed(() => session.value?.identity?.email || '未知管理身份');
function select(next: AdminView): void { view.value = next; mobileOpen.value = false; notification.value = null; error.value = ''; }
function accessLogin(): void { if (phase.value !== 'signingOut') location.assign('/admin/'); }
onMounted(() => { void state.restore(); });
</script>
<template>
  <ElConfigProvider :locale="zhCn">
    <div v-if="phase !== 'ready'" class="auth-screen">
      <div class="auth-orbit" aria-hidden="true"></div>
      <section class="auth-card" aria-label="Cloudflare Access 管理登录">
        <div class="brand-mark large"><Lock /></div><div class="eyebrow">ALLIRAAI CONSOLE</div>
        <h1>Cloudflare Access 管理登录</h1><p class="auth-subtitle">安全连接你的管理工作空间</p>
        <ElAlert v-if="error" :title="error" type="warning" :closable="false" />
        <p v-if="notification" role="status">{{ notification.text }}</p>
        <div class="auth-explanation"><span class="status-dot"></span><span>{{ phase === 'loading' ? '正在检查 Cloudflare Access 管理会话…' : '通过 Access 验证后，使用独立管理 Cookie 与 CSRF。' }}</span></div>
        <ElButton type="primary" size="large" :loading="phase === 'loading'" :disabled="phase === 'loading' || phase === 'signingOut'" @click="state.restore()"><Refresh />检查已有管理登录</ElButton>
        <ElButton size="large" :disabled="phase === 'loading' || phase === 'signingOut'" @click="accessLogin">通过 Cloudflare Access 登录<ArrowRight /></ElButton>
        <p class="auth-footnote">不接收一次性链接或开发 Bearer。实际身份验证由 Cloudflare Access 完成。</p>
      </section>
      <span class="auth-bottom">AlliraAi · 管理与普通账号严格隔离</span>
    </div>
    <div v-else class="console-shell" :class="{ 'mobile-nav-open': mobileOpen }">
      <button v-if="mobileOpen" class="nav-shade" aria-label="关闭导航遮罩" @click="mobileOpen = false"></button>
      <aside class="sidebar" aria-label="管理侧边栏">
        <div class="sidebar-brand"><div class="brand-mark">A<span></span></div><div><strong>AlliraAi</strong><small>ADMIN WORKSPACE</small></div></div>
        <nav aria-label="管理导航">
          <template v-for="tab in tabs" :key="tab.key">
            <div v-if="tab.group" class="nav-group">{{ tab.group }}</div>
            <button :class="{ active: view === tab.key }" :aria-current="view === tab.key ? 'page' : undefined" @click="select(tab.key)"><component :is="tab.icon" /><span>{{ tab.label }}</span><small v-if="view === tab.key">›</small></button>
          </template>
        </nav>
        <div class="sidebar-footer"><Lock /><div><strong>Access Protected</strong><small>独立管理 · 最小访问</small></div><span class="status-dot"></span></div>
      </aside>
      <div class="main-shell">
        <header class="topbar"><div class="breadcrumbs"><ElButton class="mobile-menu" text aria-label="打开管理导航" @click="mobileOpen = !mobileOpen"><Menu /></ElButton><span>管理工作空间</span><span class="slash">/</span><strong>{{ current.label }}</strong></div><div class="topbar-actions"><ElTag type="success" effect="plain" round size="small">Access 已验证</ElTag><span class="identity-email" :title="identity">{{ identity }}</span><ElButton text aria-label="注销管理" @click="state.logout()"><SwitchButton /><span class="logout-text">退出</span></ElButton></div></header>
        <main class="workspace" aria-label="管理内容">
          <div class="workspace-kicker"><span class="eyebrow">{{ current.caption }}</span><span class="session-expiry">管理会话至 {{ formatDate(session?.expiresAt) }}</span></div>
          <ElAlert v-if="error" class="global-notice" :title="error" type="error" :closable="false" />
          <ElAlert v-if="notification" class="global-notice" :title="notification.text" :type="notification.tone" :closable="true" @close="notification = null" />
          <ElAlert v-if="blocked" class="global-notice" title="上次操作结果不确定。管理修改已锁定，请显式刷新相关列表或审计并核对后重新确认。" type="warning" :closable="false" />
          <component :is="component" :key="`${epoch}:${view}`" :view="view" />
          <footer class="workspace-footer"><span>AlliraAi Console</span><span>只读状态不代表真实渠道可用性或生产验收</span></footer>
        </main>
      </div>
    </div>
  </ElConfigProvider>
</template>

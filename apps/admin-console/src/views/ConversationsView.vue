<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, shallowRef, watch } from 'vue';
import { ElAlert, ElButton, ElDialog, ElDrawer, ElForm, ElFormItem, ElInput, ElTable, ElTableColumn, ElTag } from 'element-plus';
import { AlliraApiError } from '@allira/client';
import type { AdminConversation, Message } from '@allira/client';
import { useAdminView, useAdminQuery, formatDate, formatNumber } from '../context';
import DangerConfirm from '../components/DangerConfirm.vue';

const ctx = useAdminView();
const ownerId = ref('');
const filterDirty = ref(false);
const online = ref(navigator.onLine !== false);
const selectedConversation = shallowRef<AdminConversation | null>(null);
const deleteTarget = shallowRef<AdminConversation | null>(null);
let filterEpoch = 0;

const conversations = useAdminQuery<AdminConversation>((cursor, signal) => {
  const userId = ownerId.value.trim();
  return ctx.client.listConversations({ cursor, limit: 30, ...(userId ? { userId } : {}), signal });
});

// This query is deliberately not loaded on mount or from a row-rendering hook.
const messages = useAdminQuery<Message>((cursor, signal) => {
  const selected = selectedConversation.value;
  if (!selected) return Promise.resolve({ items: [], nextCursor: null });
  return ctx.client.listMessages(selected.id, { cursor, limit: 50, signal });
});

const writeDisabled = computed(() => ctx.mutating.value || ctx.blocked.value || conversations.loading.value || !online.value);

function updateOnline(): void {
  online.value = navigator.onLine !== false;
}

function closeMessages(): void {
  messages.invalidate();
  selectedConversation.value = null;
}

function closeDelete(): void {
  deleteTarget.value = null;
}

function clearPrivate(): void {
  closeMessages();
  closeDelete();
}

watch(ownerId, () => {
  ++filterEpoch;
  filterDirty.value = true;
  conversations.invalidate();
  clearPrivate();
}, { flush: 'sync' });

async function refreshConversations(explicit = true): Promise<void> {
  if (!ctx.valid() || conversations.loading.value || ctx.mutating.value || !online.value) return;
  const epoch = filterEpoch;
  clearPrivate();
  const committed = await conversations.refresh();
  if (!committed || !ctx.valid() || epoch !== filterEpoch) return;
  filterDirty.value = false;
  if (explicit) ctx.reconcile();
}

async function showMessages(item: AdminConversation): Promise<void> {
  if (!ctx.valid() || filterDirty.value || conversations.loading.value || ctx.mutating.value || !online.value) return;
  closeMessages();
  selectedConversation.value = item;
  // Only this explicit click opens the private-content read path.
  await messages.refresh();
}

async function refreshMessages(): Promise<void> {
  if (!ctx.valid() || !selectedConversation.value || messages.loading.value || ctx.mutating.value || !online.value) return;
  const selected = selectedConversation.value;
  const epoch = filterEpoch;
  await messages.refresh();
  if (ctx.valid() && epoch === filterEpoch && selectedConversation.value === selected && !messages.error.value) {
    // Message detail is not a reconciliation of a potentially deleted conversation.
  }
}

function chooseDelete(item: AdminConversation): void {
  if (writeDisabled.value || filterDirty.value || conversations.loading.value || !ctx.valid()) return;
  deleteTarget.value = item;
}

async function deleteConversation(): Promise<void> {
  const target = deleteTarget.value;
  if (!target || writeDisabled.value || !ctx.valid()) return;
  const epoch = filterEpoch;
  let conflict = false;
  closeDelete();
  closeMessages();
  await ctx.mutate(async () => {
    try {
      return await ctx.client.deleteConversation(target.id, { signal: ctx.signal });
    } catch (error: unknown) {
      conflict = error instanceof AlliraApiError && error.status === 409;
      throw error;
    }
  }, async () => {
    if (!ctx.valid() || epoch !== filterEpoch) return;
    ctx.notify('会话及其消息已永久删除。', 'success');
    // A successful mutation may refresh its metadata, but must not unlock an uncertain write.
    await conversations.refresh();
  });
  if (conflict && ctx.valid() && epoch === filterEpoch) {
    ctx.notify('会话有正在运行的任务，未执行删除（409）。正在刷新列表；请核对任务状态后再决定。', 'warning');
    // Do not optimistically remove a refused deletion from the table.
    await conversations.refresh();
  }
}

function messageStatus(status: unknown): string {
  switch (status) {
    case 'generating': return '生成中';
    case 'completed': return '已完成';
    case 'failed': return '失败';
    case 'cancelled': return '已停止';
    default: return '未知';
  }
}

function messageTone(status: unknown): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'completed') return 'success';
  if (status === 'generating') return 'warning';
  if (status === 'failed') return 'danger';
  return 'info';
}

function onMessagesVisibility(visible: boolean): void {
  if (!visible) closeMessages();
}

function onDeleteVisibility(visible: boolean): void {
  if (!visible) closeDelete();
}

ctx.signal.addEventListener('abort', clearPrivate, { once: true });
onMounted(() => {
  window.addEventListener('online', updateOnline);
  window.addEventListener('offline', updateOnline);
  void refreshConversations(false);
});
onScopeDispose(() => {
  ++filterEpoch;
  clearPrivate();
  ctx.signal.removeEventListener('abort', clearPrivate);
  window.removeEventListener('online', updateOnline);
  window.removeEventListener('offline', updateOnline);
});
</script>

<template>
  <section aria-labelledby="conversations-heading">
    <div class="page-heading">
      <div>
        <h1 id="conversations-heading">会话管理</h1>
        <p class="section-subtitle">先查看会话摘要；只有点击“查看消息”才请求敏感聊天正文。</p>
      </div>
      <ElButton :loading="conversations.loading.value" :disabled="!online || ctx.mutating.value" @click="refreshConversations()">刷新会话列表</ElButton>
    </div>

    <ElForm class="toolbar" inline aria-label="会话筛选" @submit.prevent="refreshConversations()">
      <ElFormItem label="所有者 ID">
        <ElInput v-model="ownerId" name="conversationUserId" placeholder="留空查看全部所有者" clearable maxlength="100" :disabled="ctx.mutating.value" />
      </ElFormItem>
      <ElFormItem>
        <ElButton type="primary" native-type="submit" :loading="conversations.loading.value" :disabled="!online || ctx.mutating.value">查询会话</ElButton>
      </ElFormItem>
      <span v-if="filterDirty" class="muted" role="status">筛选已更改，请查询以加载列表。</span>
    </ElForm>

    <ElAlert v-if="!online" title="当前离线，修改操作已停用。联网后请显式刷新核对状态。" type="warning" :closable="false" show-icon />
    <ElAlert v-if="conversations.error.value" :title="conversations.error.value" type="error" :closable="false" show-icon />

    <div class="data-card">
      <ElTable :data="conversations.items.value" row-key="id" empty-text="暂无会话，或尚未加载当前筛选。" :aria-busy="conversations.loading.value" table-layout="auto">
        <!-- @vue-generic {AdminConversation} -->
        <ElTableColumn label="会话" min-width="240">
          <template #default="{ row }">
            <div class="conversation-title">{{ row.title || '未命名会话' }}</div>
            <div class="mono muted resource-id">{{ row.id }}</div>
          </template>
        </ElTableColumn>
        <!-- @vue-generic {AdminConversation} -->
        <ElTableColumn label="所有者" min-width="220">
          <template #default="{ row }"><span class="mono resource-id">{{ row.ownerId }}</span></template>
        </ElTableColumn>
        <!-- @vue-generic {AdminConversation} -->
        <ElTableColumn label="消息数" min-width="100" align="right">
          <template #default="{ row }">{{ formatNumber(row.messageCount) }}</template>
        </ElTableColumn>
        <!-- @vue-generic {AdminConversation} -->
        <ElTableColumn label="更新时间" min-width="180">
          <template #default="{ row }">{{ formatDate(row.updatedAt) }}</template>
        </ElTableColumn>
        <!-- @vue-generic {AdminConversation} -->
        <ElTableColumn label="操作" min-width="180" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" :disabled="filterDirty || conversations.loading.value || ctx.mutating.value || !online" @click="showMessages(row)">查看消息</ElButton>
            <ElButton link type="danger" :disabled="writeDisabled || filterDirty || conversations.loading.value" @click="chooseDelete(row)">删除会话</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
      <div class="table-footer">
        <span class="muted" role="status">已显示 {{ formatNumber(conversations.items.value.length) }} 条会话摘要</span>
        <ElButton :loading="conversations.loading.value" :disabled="!conversations.nextCursor.value || filterDirty || !online || ctx.mutating.value" @click="conversations.loadMore()">加载更多会话</ElButton>
      </div>
    </div>

    <ElDrawer :model-value="selectedConversation !== null" title="会话消息 · 敏感内容" size="min(960px, 96vw)" destroy-on-close @update:model-value="onMessagesVisibility">
      <template v-if="selectedConversation">
        <h2 class="private-title">{{ selectedConversation.title || '未命名会话' }}</h2>
        <p class="mono muted resource-id">{{ selectedConversation.id }}</p>
        <ElAlert title="聊天正文仅在本抽屉中以纯文本展示；关闭、切换会话或更改所有者筛选即清空，不预取其他会话。" type="warning" :closable="false" show-icon />
        <div class="toolbar detail-toolbar">
          <ElButton :loading="messages.loading.value" :disabled="!online || ctx.mutating.value" @click="refreshMessages">刷新消息</ElButton>
          <ElButton @click="closeMessages">关闭并清空敏感内容</ElButton>
        </div>
        <ElAlert v-if="messages.error.value" :title="messages.error.value" type="error" :closable="false" show-icon />
        <ElTable :data="messages.items.value" row-key="id" empty-text="暂无消息，或正在读取。" :aria-busy="messages.loading.value" table-layout="auto">
          <!-- @vue-generic {Message} -->
          <ElTableColumn label="角色" width="78">
            <template #default="{ row }">{{ row.role === 'user' ? '用户' : row.role === 'assistant' ? '助手' : '未知' }}</template>
          </ElTableColumn>
          <!-- @vue-generic {Message} -->
          <ElTableColumn label="状态" width="96">
            <template #default="{ row }"><ElTag :type="messageTone(row.status)" effect="light">{{ messageStatus(row.status) }}</ElTag></template>
          </ElTableColumn>
          <!-- @vue-generic {Message} -->
          <ElTableColumn label="正文（按时间正序）" min-width="300">
            <template #default="{ row }"><pre class="message-content">{{ row.content }}</pre></template>
          </ElTableColumn>
          <!-- @vue-generic {Message} -->
          <ElTableColumn label="时间" min-width="170">
            <template #default="{ row }">{{ formatDate(row.createdAt) }}</template>
          </ElTableColumn>
        </ElTable>
        <div class="table-footer">
          <span class="muted" role="status">已显示 {{ formatNumber(messages.items.value.length) }} 条消息</span>
          <ElButton :loading="messages.loading.value" :disabled="!messages.nextCursor.value || !online || ctx.mutating.value" @click="messages.loadMore()">加载更多消息</ElButton>
        </div>
      </template>
    </ElDrawer>

    <ElDialog :model-value="deleteTarget !== null" title="永久删除会话" width="min(560px, 94vw)" destroy-on-close :close-on-click-modal="false" @update:model-value="onDeleteVisibility">
      <template v-if="deleteTarget">
        <p class="private-title">{{ deleteTarget.title || '未命名会话' }}</p>
        <p class="mono resource-id">{{ deleteTarget.id }}</p>
        <p class="muted">所有者：{{ deleteTarget.ownerId }}</p>
        <DangerConfirm :key="deleteTarget.id" title="确认永久删除此会话及消息" description="此操作不可撤销。运行中的会话会被服务端拒绝（409），不会为了删除而自动停止生成。" confirm-label="永久删除会话" :disabled="writeDisabled" :busy="ctx.mutating.value" @confirm="deleteConversation" />
      </template>
    </ElDialog>
  </section>
</template>

<style scoped>
.resource-id { overflow-wrap: anywhere; }
.conversation-title { font-weight: 600; overflow-wrap: anywhere; }
.private-title { white-space: pre-wrap; overflow-wrap: anywhere; }
.detail-toolbar { margin-block: 18px; }
.message-content { margin: 0; font: inherit; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
</style>

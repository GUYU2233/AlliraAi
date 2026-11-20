<script setup lang="ts">
import { ref, watch } from 'vue';
import { ElButton, ElCheckbox, ElPopover } from 'element-plus';
const props = withDefaults(defineProps<{ title: string; description: string; confirmLabel?: string; disabled?: boolean; busy?: boolean }>(), { confirmLabel: '确认执行', disabled: false, busy: false });
const emit = defineEmits<{ confirm: [] }>();
const open = ref(false), consent = ref(false);
watch(open, () => { consent.value = false; });
watch(() => props.disabled, value => { if (value) { open.value = false; consent.value = false; } });
function confirm(): void {
  if (!consent.value || props.disabled || props.busy) return;
  consent.value = false; open.value = false; emit('confirm');
}
</script>
<template>
  <ElPopover v-model:visible="open" trigger="click" :width="300" :disabled="disabled" placement="bottom-end">
    <template #reference><ElButton type="danger" plain size="small" :disabled="disabled" :loading="busy">{{ title }}</ElButton></template>
    <div class="danger-confirm" role="group" :aria-label="title">
      <strong>{{ title }}</strong><p>{{ description }}</p>
      <ElCheckbox v-model="consent" :disabled="busy">我已了解影响并确认</ElCheckbox>
      <div class="confirm-actions"><ElButton size="small" @click="open = false">取消</ElButton><ElButton type="danger" size="small" :disabled="!consent || disabled" :loading="busy" @click="confirm">{{ confirmLabel }}</ElButton></div>
    </div>
  </ElPopover>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ title?: string; open?: boolean }>(), {
  title: '技术详情',
  open: false,
})
</script>

<template>
  <details class="technical-details" :open="open">
    <summary>{{ title }}</summary>
    <div class="technical-details__body">
      <slot />
    </div>
  </details>
</template>

<style scoped>
.technical-details {
  margin-top: 16px;
  border-top: 1px solid var(--border, #e5e7eb);
  color: var(--text-secondary, #5f6675);
}

.technical-details summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary, #5f6675);
  outline: none;
}

.technical-details summary:focus-visible {
  border-radius: 8px;
  box-shadow: 0 0 0 3px rgb(79 70 229 / 20%);
}

.technical-details__body {
  padding: 4px 0 12px;
  overflow-wrap: anywhere;
}
</style>

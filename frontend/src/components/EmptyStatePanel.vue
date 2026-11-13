<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  actionLabel?: string
}>()

defineEmits<{ action: [] }>()
</script>

<template>
  <div class="empty-state" role="status">
    <div class="empty-state-icon" aria-hidden="true"><slot name="icon">·</slot></div>
    <strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>
    <el-button v-if="actionLabel" @click="$emit('action')">{{ actionLabel }}</el-button>
    <slot />
  </div>
</template>

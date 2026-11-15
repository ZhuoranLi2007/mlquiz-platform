<script setup lang="ts">
import { computed } from 'vue'
import { statusLabel } from '../utils/presentation'

const props = defineProps<{ status?: string | null; label?: string; tone?: 'neutral' | 'info' | 'success' | 'warning' | 'danger' }>()

const resolvedTone = computed(() => {
  if (props.tone) return props.tone
  if (['completed', 'complete', 'submitted', 'ready', 'verified', 'corrected'].includes(props.status || '')) return 'success'
  if (['failed', 'cancelled', 'overdue'].includes(props.status || '')) return 'danger'
  if (['pending', 'queued', 'due'].includes(props.status || '')) return 'warning'
  if (['running', 'in_progress', 'active', 'processing'].includes(props.status || '')) return 'info'
  return 'neutral'
})
</script>

<template>
  <span class="status-badge" :class="`status-badge--${resolvedTone}`">
    <span class="status-badge__dot" aria-hidden="true" />
    {{ label || statusLabel(status) }}
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 3px 10px;
  border-radius: 999px;
  background: #f3f4f6;
  color: #4b5563;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

.status-badge__dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.status-badge--info { background: #eef2ff; color: #4338ca; }
.status-badge--success { background: #ecfdf5; color: #047857; }
.status-badge--warning { background: #fffbeb; color: #b45309; }
.status-badge--danger { background: #fef2f2; color: #b91c1c; }
</style>

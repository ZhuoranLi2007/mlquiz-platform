<script setup lang="ts">
/** 状态标签：语义色 + 中文标签，颜色只是辅助，文字才是信息。 */
import { computed } from 'vue'

const props = defineProps<{ status: string; label?: string }>()

const FALLBACK: Record<string, string> = {
  queued: '排队中',
  parsing: '解析中',
  parsed: '已解析（答案待生成）',
  generating: '生成答案中',
  partial: '部分完成',
  completed: '完成',
  failed: '失败',
}

const tone = computed(() => {
  switch (props.status) {
    case 'completed': return 'ok'
    case 'partial':
    case 'parsed': return 'warn'
    case 'failed': return 'danger'
    case 'queued':
    case 'parsing':
    case 'generating': return 'info'
    default: return ''
  }
})

const text = computed(() => props.label || FALLBACK[props.status] || props.status)
</script>

<template>
  <span class="pill" :class="tone">{{ text }}</span>
</template>

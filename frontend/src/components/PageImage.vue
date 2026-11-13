<script setup lang="ts">
import { computed } from 'vue'
import { pageImageUrl } from '../api/client'

const props = defineProps<{ docId: string; page: number; label?: string }>()
const url = computed(() => pageImageUrl(props.docId, props.page))
</script>

<template>
  <div v-if="docId && page">
    <div class="small muted" style="margin-bottom: 6px">
      原页预览 · {{ label || `第 ${page} 页` }}
      <a :href="url" target="_blank" rel="noopener" style="margin-left: 8px">在新窗口打开</a>
    </div>
    <img class="page-img" :src="url" :alt="`原页 ${page}`" loading="lazy" />
  </div>
</template>

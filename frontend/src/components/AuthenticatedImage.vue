<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { protectedBlobUrl } from '../api/client'

const props = defineProps<{ src: string; alt: string }>()
const objectUrl = ref('')
const failed = ref(false)
let request = 0

watch(() => props.src, async (src) => {
  const current = ++request
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  objectUrl.value = ''
  failed.value = false
  if (!src) return
  try {
    const url = await protectedBlobUrl(src)
    if (current !== request) URL.revokeObjectURL(url)
    else objectUrl.value = url
  } catch {
    if (current === request) failed.value = true
  }
}, { immediate: true })

onBeforeUnmount(() => {
  request++
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
})
</script>

<template>
  <img v-if="objectUrl" :src="objectUrl" :alt="alt" />
  <span v-else-if="failed" class="image-error">图片暂时无法加载</span>
</template>

<style scoped>
.image-error{display:inline-block;padding:8px;color:#8e5050;font-size:13px}
</style>

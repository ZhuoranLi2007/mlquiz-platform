<script setup lang="ts">
import { computed } from 'vue'
import MathText from './MathText.vue'
import AuthenticatedImage from './AuthenticatedImage.vue'
const props = defineProps<{ question: any }>()

function rows(value: any) { return Array.isArray(value) ? value : Object.values(value || {}) }

const hasSourceMaterial = computed(() => {
  const question = props.question || {}
  const shared = rows(question.shared_materials).some((row: any) =>
    (row.blocks || []).some((block: any) =>
      (block.fragments || []).some((fragment: any) => fragment?.type !== 'text')))
  const sourceMedia = [...(question.source_blocks || []),
    ...rows(question.source_options).flatMap((row: any) => row.blocks || [])]
    .some((block: any) => (block.fragments || []).some((fragment: any) => fragment?.type !== 'text'))
  if (shared || sourceMedia) return true
  // Text-only snapshots commonly repeat the already-rendered stem and
  // options.  Only surface original material when it adds media/shared
  // context or the backend explicitly reports a content difference.
  return false
})
const hasDistinctSource = computed(() => Boolean(props.question?.content_warning) || hasSourceMaterial.value)
const warningText = computed(() => {
  const warning = String(props.question?.content_warning || '')
  return warning.startsWith('原题资料暂不可用') ? '原题资料暂不可用' : warning
})
</script>
<template>
  <section v-if="hasDistinctSource" class="source-content">
    <p v-if="question.content_warning" class="warning">{{ warningText }}</p>
    <details v-if="hasSourceMaterial"><summary>查看原题资料</summary>
      <section v-for="shared in question.shared_materials || []" :key="shared.shared_material_id">
        <h4>共享材料</h4>
        <p v-for="(block, index) in shared.blocks" :key="index">
          <template v-for="(fragment, j) in block.fragments" :key="j">
            <MathText v-if="fragment.type === 'text'" :text="fragment.text" />
            <AuthenticatedImage v-else-if="fragment.available" :src="fragment.url" :alt="fragment.alt_text" />
            <span v-else class="warning">[媒体不可用：{{ fragment.reason }}]</span>
          </template>
        </p>
      </section>
      <p v-for="(block, index) in question.source_blocks || []" :key="index">
        <template v-for="(fragment, j) in block.fragments" :key="j">
          <MathText v-if="fragment.type === 'text'" :text="fragment.text" />
          <AuthenticatedImage v-else-if="fragment.available" :src="fragment.url" :alt="fragment.alt_text" />
          <span v-else class="warning">[媒体不可用：{{ fragment.reason }}]</span>
        </template>
      </p>
      <div v-for="option in question.source_options || []" :key="option.key">
        <b>{{ option.key }}</b>
        <p v-for="(block, index) in option.blocks" :key="index">
          <template v-for="(fragment, j) in block.fragments" :key="j">
            <MathText v-if="fragment.type === 'text'" :text="fragment.text" />
            <AuthenticatedImage v-else-if="fragment.available" :src="fragment.url" :alt="fragment.alt_text" />
            <span v-else class="warning">[媒体不可用：{{ fragment.reason }}]</span>
          </template>
        </p>
      </div>
    </details>
  </section>
</template>
<style scoped>
.source-content{margin:10px 0;padding:9px 11px;border:1px solid #dde5ed;border-radius:8px;background:#fbfcfe;line-height:1.7}.source-content img{max-width:100%;max-height:500px;vertical-align:middle}.warning{color:#986008;background:#fff5db;padding:4px}.source-content p{white-space:pre-wrap}.source-content summary{color:#52647d;font-size:11px;font-weight:700;cursor:pointer}
</style>

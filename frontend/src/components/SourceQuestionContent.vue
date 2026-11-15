<script setup lang="ts">
import { computed } from 'vue'
import MathText from './MathText.vue'
import AuthenticatedImage from './AuthenticatedImage.vue'

const props = withDefaults(defineProps<{
  question: any
  part?: 'stem' | 'option'
  optionKey?: string
  fallback?: string
}>(), { part: 'stem', optionKey: '', fallback: '' })

type Fragment = { type: string; text?: string; available?: boolean; url?: string;
  alt_text?: string; role?: string }
type Block = { fragments?: Fragment[] }

function rows(value: any): any[] {
  return Array.isArray(value) ? value : Object.values(value || {})
}
function fragments(block: Block): Fragment[] {
  const result: Fragment[] = []
  for (const fragment of block.fragments || []) {
    if (fragment.type === 'text' && result.at(-1)?.type === 'text') {
      result[result.length - 1].text += fragment.text || ''
    } else result.push({ ...fragment })
  }
  return result
}
function hasMedia(blocks: Block[]) {
  return blocks.some((block) => fragments(block).some((fragment) => fragment.type === 'media'))
}
function mediaReady(blocks: Block[]) {
  return blocks.every((block) => fragments(block).every((fragment) =>
    fragment.type !== 'media' || (fragment.available && fragment.url)))
}
function sourceText(text: string | undefined, blockIndex: number, fragmentIndex: number) {
  const value = text || ''
  if (props.part !== 'option' || blockIndex !== 0 || fragmentIndex !== 0 || !props.optionKey) return value
  const key = props.optionKey.replace(/[^A-Z0-9]/gi, '')
  if (!key) return value
  return value.replace(new RegExp(`^\\s*(?:[（(]${key}[)）]|${key}[.、．:：])\\s*`, 'i'), '')
}

const sourceBlocks = computed<Block[]>(() => {
  if (props.part === 'stem') return rows(props.question?.source_blocks)
  const option = rows(props.question?.source_options).find((row: any) =>
    String(row.key || '').toUpperCase() === props.optionKey.toUpperCase())
  return rows(option?.blocks)
})
const useSourceBlocks = computed(() => hasMedia(sourceBlocks.value) && mediaReady(sourceBlocks.value))
const sharedBlocks = computed<Block[]>(() => props.part === 'stem'
  ? rows(props.question?.shared_materials).flatMap((row: any) => rows(row.blocks)) : [])
const showShared = computed(() => sharedBlocks.value.length > 0 && mediaReady(sharedBlocks.value))
const missingMedia = computed(() =>
  (hasMedia(sourceBlocks.value) && !mediaReady(sourceBlocks.value)) ||
  (sharedBlocks.value.length > 0 && !mediaReady(sharedBlocks.value)) ||
  (props.part === 'stem' && !sourceBlocks.value.length && Boolean(props.question?.media_incomplete)))
const fallbackText = computed(() => props.fallback || (props.part === 'stem'
  ? String(props.question?.stem || '') : ''))
</script>

<template>
  <span class="question-content">
    <span v-if="showShared" class="source-shared">
      <span v-for="(block, index) in sharedBlocks" :key="index" class="source-line">
        <template v-for="(fragment, partIndex) in fragments(block)" :key="partIndex">
          <MathText v-if="fragment.type === 'text'" :text="fragment.text" />
          <AuthenticatedImage v-else-if="fragment.available && fragment.url"
            class="source-media" :class="{ 'source-formula': fragment.role === 'formula' }"
            :src="fragment.url" :alt="fragment.alt_text || '题目配图或公式'" />
        </template>
      </span>
    </span>
    <span v-if="useSourceBlocks" class="source-lines">
      <span v-for="(block, index) in sourceBlocks" :key="index" class="source-line">
        <template v-for="(fragment, partIndex) in fragments(block)" :key="partIndex">
          <MathText v-if="fragment.type === 'text'" :text="sourceText(fragment.text, index, partIndex)" />
          <AuthenticatedImage v-else-if="fragment.available && fragment.url"
            class="source-media" :class="{ 'source-formula': fragment.role === 'formula' }"
            :src="fragment.url" :alt="fragment.alt_text || '题目配图或公式'" />
        </template>
      </span>
    </span>
    <MathText v-else :text="fallbackText" />
    <span v-if="missingMedia" class="media-note">题目图片暂无法显示</span>
  </span>
</template>

<style scoped>
.question-content{display:inline;line-height:inherit}.source-shared{display:block;margin-bottom:8px;padding:8px 10px;border-left:3px solid #c8d8eb;background:#f7f9fd}.source-lines{display:inline}.source-line{display:block}.source-line+.source-line{margin-top:6px}.source-line :deep(.source-media){display:inline-block;max-width:100%;height:auto;vertical-align:-.25em}.source-line :deep(.source-formula){max-height:3.8em;object-fit:contain;object-position:left center}.media-note{display:block;margin-top:6px;color:#95651e;font-size:14px;font-weight:650}
</style>

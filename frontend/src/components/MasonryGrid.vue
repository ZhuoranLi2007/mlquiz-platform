<script setup lang="ts">
/**
 * 通用响应式瀑布流（Masonry）。
 *
 * 不用 CSS columns（阅读顺序会变成竖排），也不用普通 Grid（同排会被强制等高）。
 * 做法：所有卡片只渲染一次并绝对定位，测量每张卡真实高度后按原始顺序
 * 「贪心放入当前较短的一列」，从而自动补位、不留人为大块空白。
 * ResizeObserver 负责卡片内容变化（公式字体加载、展开解析）与容器宽度变化。
 * 默认桌面两列；调用方可配置三列，并通过 twoColumnBelow / singleColumnBelow
 * 在中等宽度和窄屏分别切为两列、单列。
 */
import {
  computed, nextTick, onBeforeUnmount, onMounted, ref, watch,
} from 'vue'

const props = withDefaults(defineProps<{
  items: any[]
  gap?: number
  maxColumns?: number
  twoColumnBelow?: number
  singleColumnBelow?: number
  itemKey?: string
}>(), {
  gap: 16,
  maxColumns: 2,
  twoColumnBelow: 1050,
  singleColumnBelow: 700,
  itemKey: '',
})

const emit = defineEmits<{
  (e: 'layout', info: {
    columns: number
    columnHeights: number[]
    count: number
    maxGap: number
  }): void
}>()

const wrap = ref<HTMLElement | null>(null)
const itemEls = ref<(HTMLElement | null)[]>([])
const positions = ref<Array<{ x: number; y: number }>>([])
const wrapHeight = ref(0)
const columnCount = ref(props.maxColumns)
const columnHeights = ref<number[]>([])
const laidOut = ref(false)

function setItemRef(el: any, index: number) {
  itemEls.value[index] = (el as HTMLElement) || null
}

function keyFor(item: any, index: number) {
  return props.itemKey ? item?.[props.itemKey] ?? index : index
}

let ro: ResizeObserver | null = null
let raf = 0
let disposed = false

async function relayout() {
  // The grid is often mounted immediately after an asynchronous bank request.
  // Wait for function refs and the surrounding route transition to settle before
  // measuring; otherwise the first pass can see width/height 0 and leave the
  // absolutely positioned wrapper at height 0 until another filter mutation.
  await nextTick()
  const el = wrap.value
  if (!el || disposed) return
  const width = el.clientWidth
  if (width <= 0) {
    schedule()
    return
  }

  const cols = width < props.singleColumnBelow
    ? 1
    : props.maxColumns > 2 && width < props.twoColumnBelow
      ? 2
      : Math.max(1, props.maxColumns)
  const gap = props.gap
  const colWidth = Math.max(120, (width - gap * (cols - 1)) / cols)
  el.style.setProperty('--masonry-col-width', `${colWidth}px`)
  columnCount.value = cols

  await nextTick()
  if (disposed) return

  const heights = new Array(cols).fill(0) as number[]
  const next: Array<{ x: number; y: number }> = []
  for (let i = 0; i < props.items.length; i += 1) {
    const node = itemEls.value[i]
    const h = node ? node.getBoundingClientRect().height : 0
    // 等高时优先靠左的列，保证阅读顺序稳定
    let col = 0
    for (let j = 1; j < cols; j += 1) {
      if (heights[j] < heights[col] - 0.5) col = j
    }
    next.push({ x: col * (colWidth + gap), y: heights[col] })
    heights[col] += h + gap
  }
  positions.value = next
  const finals = heights.map((h) => Math.max(0, Math.round(h - gap)))
  columnHeights.value = finals
  wrapHeight.value = finals.length ? Math.max(...finals) : 0
  laidOut.value = true
  emit('layout', {
    columns: cols, columnHeights: finals, count: props.items.length,
    maxGap: cols > 1 ? Math.max(...finals) - Math.min(...finals) : 0,
  })
}

function schedule() {
  if (raf || disposed) return
  raf = requestAnimationFrame(() => {
    raf = 0
    void relayout()
  })
}

function reobserve() {
  if (!ro || !wrap.value) return
  ro.disconnect()
  ro.observe(wrap.value)
  for (const el of itemEls.value) {
    if (el) ro.observe(el)
  }
}

onMounted(async () => {
  ro = new ResizeObserver(schedule)
  await nextTick()
  reobserve()
  schedule()
  // 字体加载完成后高度会变，再校准一次
  if ((document as any).fonts?.ready) {
    void (document as any).fonts.ready.then(() => { if (!disposed) schedule() })
  }
})

watch(() => props.items.map((item, index) => keyFor(item, index)).join('|'), async () => {
  laidOut.value = false
  itemEls.value.length = props.items.length
  await nextTick()
  reobserve()
  schedule()
}, { flush: 'post' })

onBeforeUnmount(() => {
  disposed = true
  if (raf) cancelAnimationFrame(raf)
  if (ro) ro.disconnect()
})

const debugHeights = computed(() => columnHeights.value.join(','))
</script>

<template>
  <div
    ref="wrap"
    class="masonry"
    :class="{ 'is-ready': laidOut }"
    :style="{ height: `${wrapHeight}px`, '--masonry-fallback-cols': maxColumns }"
    :data-columns="columnCount"
    :data-col-heights="debugHeights"
    :data-item-count="items.length"
  >
    <div
      v-for="(item, index) in items"
      :key="keyFor(item, index)"
      :ref="(el) => setItemRef(el, index)"
      class="masonry-item"
      :data-col-index="index"
      :style="{
        transform: `translate3d(${positions[index]?.x || 0}px, ${positions[index]?.y || 0}px, 0)`,
      }"
    >
      <slot :item="item" :index="index" />
    </div>
  </div>
</template>

<style scoped>
.masonry {
  position: relative;
  width: 100%;
  min-height: 0;
}
.masonry-item {
  position: absolute;
  top: 0;
  left: 0;
  width: var(--masonry-col-width, 100%);
  box-sizing: border-box;
}
.masonry:not(.is-ready) {
  display: grid;
  grid-template-columns: repeat(var(--masonry-fallback-cols, 3), minmax(0, 1fr));
  gap: 16px;
  height: auto !important;
}
.masonry:not(.is-ready) .masonry-item {
  position: relative;
  width: auto;
  transform: none !important;
}
.masonry.is-ready .masonry-item {
  transition: transform 0.22s ease;
}
@media (max-width: 680px) {
  .masonry:not(.is-ready) { grid-template-columns: 1fr; }
  /* 极窄屏切为单列时直接归位，避免大量卡片的过渡位置短暂撑宽页面。 */
  .masonry.is-ready .masonry-item { transition: none; }
}
@media (prefers-reduced-motion: reduce) {
  .masonry.is-ready .masonry-item { transition: none; }
}
</style>

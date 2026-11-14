<script setup lang="ts">
import { computed } from 'vue'
import katex from 'katex'
import { normalizeDisplayMath } from '../utils/mathDisplay'

const props = defineProps<{ text?: string }>()
type Part = { type: 'html' | 'text'; value: string; display?: boolean; failed?: boolean }

// Real model output mixes delimiters: prompt says `$...$` / `$$...$$`, but a
// model will still emit `\[ ... \]` or `\( ... \)`, and sometimes wraps
// `$$...$$` *inside* `\[ ... \]`. Left as-is, the delimiters render as literal
// text. Normalise first, then parse one canonical form.
const ROW_BREAK = '\u0001'   // protects LaTeX `\\` (matrix/array row breaks)

function normalize(src: string): string {
  let s = src.replace(/\\\\/g, ROW_BREAK)
  // \[\n$$...$$\n\]  ->  $$...$$   (double-wrapped display math)
  s = s.replace(/\\\[\s*\$\$/g, '$$').replace(/\$\$\s*\\\]/g, '$$')
  s = s.replace(/\\\[/g, '$$').replace(/\\\]/g, '$$')
  s = s.replace(/\\\(/g, '$').replace(/\\\)/g, '$')
  return normalizeDisplayMath(s.split(ROW_BREAK).join('\\\\'))
}

const parts = computed<Part[]>(() => {
  const src = normalize(props.text || '')
  const out: Part[] = []
  // 行内公式允许跨行（原先把 \n 排除，长公式会原样显示），但限制长度避免吞段
  const re = /\$\$([\s\S]+?)\$\$|\$([^$]{1,600}?)\$/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(src)) !== null) {
    if (m.index > last) out.push({ type: 'text', value: src.slice(last, m.index) })
    const expr = (m[1] ?? m[2] ?? '').trim()
    if (!expr) {                                  // `$$$$` / `$ $` -> literal
      out.push({ type: 'text', value: m[0] })
      last = re.lastIndex
      continue
    }
    // 块级环境即使被单个 $ 包着，也按独立公式排版：
    // array/aligned/cases 这类表格和推导，挤在正文里会完全错位。
    const blockEnv = /\\begin\{(array|aligned|alignedat|cases|gather|split)\}/.test(expr)
    const displayMode = Boolean(m[1]) || blockEnv
    try {
      // throwOnError: 失败时抛出来，由下面记录并回退原文；
      // 关掉的话 KaTeX 会渲染成红色错误串，学生看到的是另一种乱码。
      out.push({
        type: 'html',
        display: displayMode,
        value: katex.renderToString(expr, {
          displayMode, throwOnError: true, trust: false,
          strict: false,
        }),
      })
    } catch (err) {
      // 渲染不了就保留原始 LaTeX，同时留下错误记录，方便定位是哪条公式
      console.warn('[MathText] 公式渲染失败，已保留原文', {
        expr: expr.slice(0, 120),
        error: String(err).slice(0, 200),
      })
      out.push({ type: 'text', value: m[0], failed: true } as Part)
    }
    last = re.lastIndex
  }
  if (last < src.length) out.push({ type: 'text', value: src.slice(last) })
  return out
})
</script>

<template>
  <span class="mathtext">
    <template v-for="(p, i) in parts" :key="i">
      <span v-if="p.type === 'html'" :class="{ 'math-block': p.display }"
        v-html="p.value" />
      <span v-else-if="p.failed" class="mathtext-failed" style="white-space: pre-wrap"
        title="这条公式未能渲染，已保留原始内容">{{ p.value }}</span>
      <span v-else style="white-space: pre-wrap">{{ p.value }}</span>
    </template>
  </span>
</template>

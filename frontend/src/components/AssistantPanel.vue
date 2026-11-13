<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'

const props = withDefaults(defineProps<{
  modelValue: boolean
  modelStatus: string
  mode?: 'practice' | 'quiz'
  context?: Record<string, unknown>
  lockedMode?: boolean
}>(), {
  mode: 'practice',
  context: () => ({}),
  lockedMode: false,
})
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

type Msg = { role: 'me' | 'assistant'; text: string; refs?: any[]; engine?: string }
const messages = ref<Msg[]>([])
const input = ref('')
const mode = ref<'practice' | 'quiz'>(props.mode)
const busy = ref(false)
const error = ref('')
const sessionId = 'local-assistant'

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

watch(() => props.mode, (value) => { mode.value = value })

watch(() => props.modelValue, (v) => {
  if (v && messages.value.length === 0) {
    messages.value.push({
      role: 'assistant',
      text: '我是课程助手。可以问某个知识点、当前题目的思路，或让我给出资料出处。'
        + '\n我会基于本地资料检索回答；资料没有依据时会明确说明。'
        + '\n注意：「我懂了」不会被记成掌握状态。',
    })
  }
})

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  if (mode.value === 'quiz') {
    error.value = '测验模式下助手已明确禁用，避免答案泄露。请切换到学习模式或先交卷。'
    return
  }
  error.value = ''
  messages.value.push({ role: 'me', text })
  input.value = ''
  busy.value = true
  try {
    const r: any = await api.post('/assistant/chat', {
      message: text, session_id: sessionId, mode: mode.value, context: props.context,
    })
    messages.value.push({
      role: 'assistant', text: r.answer || r.note || '（无返回内容）',
      refs: r.source_refs || [], engine: r.engine,
    })
  } catch (e: any) {
    const msg = friendlyError(e)
    messages.value.push({
      role: 'assistant',
      text: msg.includes('404')
        ? '助手接口尚未接入（阶段2）。当前不提供模拟回答。'
        : '助手调用失败：' + msg + '\n已有答卷与确定成绩不会受影响。',
    })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-drawer v-model="visible" title="学习助手" size="min(430px, 100%)" direction="rtl">
    <div class="hint" v-if="modelStatus !== 'configured'">
      模型 API 尚未配置：助手目前只能给出基于本地检索的确定性回答，
      不会用模拟结果冒充真实模型调用。
    </div>
    <div v-if="!lockedMode" class="assistant-mode">
      <span class="small muted">模式</span>
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="practice">学习模式</el-radio-button>
        <el-radio-button value="quiz">测验模式（禁用）</el-radio-button>
      </el-radio-group>
    </div>
    <div class="hint" v-if="mode === 'quiz'">
      考试模式下助手被明确禁用；这是简单但可靠的答案隔离实现。
    </div>
    <div class="chat-list" aria-live="polite">
      <div v-for="(m, i) in messages" :key="i" class="chat-row" :class="{ me: m.role === 'me' }">
        <div class="who">{{ m.role === 'me' ? '我' : '助手' }}</div>
        <div class="bubble">{{ m.text }}</div>
        <div v-if="m.refs && m.refs.length">
          <span v-for="(r, j) in m.refs" :key="j" class="ref-chip">
            {{ r.rel_path || r.document_id }} {{ r.page ? 'p.' + r.page : '' }}
          </span>
        </div>
      </div>
    </div>
    <div v-if="error" class="hint" style="margin-top: 8px">{{ error }}</div>
    <div class="assistant-composer">
      <el-input v-model="input" type="textarea" :rows="3" placeholder="输入问题，Ctrl+Enter 发送"
        :disabled="mode === 'quiz'" @keydown.ctrl.enter.prevent="send" />
      <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px">
        <span class="small muted">回答会附出处；无课程依据时会说明依据不足</span>
        <el-button type="primary" size="small" :loading="busy" :disabled="mode === 'quiz'"
          @click="send">发送</el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.assistant-mode {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.assistant-composer { margin-top: 12px; }

@media (max-width: 640px) {
  .assistant-mode { align-items: flex-start; flex-direction: column; }
}
</style>

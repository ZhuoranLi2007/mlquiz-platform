<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'

const props = defineProps<{ points: any[] }>()
const emit = defineEmits<{ saved: [] }>()
const open = ref(false)
const original = ref<any>(null)
const busy = ref(false)
const error = ref('')
const form = reactive({ type: 'single_choice', stem: '', kp: '', tags: [] as string[],
  difficulty: '中等', score: 2, options: [] as { key: string; text: string }[],
  keys: [] as string[], truth: true, explanation: '' })
const isChoice = computed(() => form.type !== 'true_false')
function show(row?: any) {
  original.value = row
  error.value = ''
  Object.assign(form, { type: row?.question_type || 'single_choice', stem: row?.stem || '',
    kp: row?.main_kp_id || '', tags: [...(row?.aux_kp_ids || [])],
    difficulty: ({ easy: '基础', hard: '困难' } as Record<string,string>)[row?.difficulty_band] || '中等',
    score: row?.score || 2, options: row?.options?.length
      ? row.options.map((o: any) => ({ ...o })) : [{ key: 'A', text: '' }, { key: 'B', text: '' }],
    keys: [...(row?.answer_payload?.keys || (row?.answer_payload?.key ? [row.answer_payload.key] : []))],
    truth: row?.answer_payload?.value ?? true, explanation: row?.explanation || '' })
  open.value = true
}
defineExpose({ show })
function addOption() {
  const key = [...'ABCDEFGH'].find(key => !form.options.some(o => o.key === key))
  if (key) form.options.push({ key, text: '' })
}
async function submit() {
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    if (!form.kp) throw new Error('请选择主知识点')
    if (!form.stem.trim() || !form.explanation.trim()) throw new Error('请填写题干和答案解析')
    if (isChoice.value && (form.options.some(o => !o.text.trim()) ||
      form.keys.length < (form.type === 'multi_choice' ? 2 : 1))) throw new Error('请补全选项，并选定正确答案')
    const body = { question_type: form.type, stem: form.stem, main_kp_id: form.kp,
      aux_kp_ids: form.tags, difficulty: form.difficulty, score: form.score, explanation: form.explanation,
      options: isChoice.value ? form.options.map(o => ({ key: o.key, text: o.text })) : [],
      answer_payload: form.type === 'true_false' ? { value: form.truth } : form.type === 'multi_choice'
        ? { ...original.value?.answer_payload, keys: form.keys } : { key: form.keys[0] },
      question_version: original.value?.question_version }
    if (original.value) await api.put('/teacher/question-bank/' + original.value.question_id, body)
    else await api.post('/teacher/question-bank', body)
    emit('saved')
    ElMessage.success('题目已保存')
    open.value = false
  } catch (e) { error.value = friendlyError(e, '操作失败，请重试') }
  finally { busy.value = false }
}
</script>
<template>
  <el-dialog v-model="open" :title="original ? '编辑题目' : '新增题目'"
    width="min(620px, 94vw)" top="5vh" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
    <el-form label-position="top" :disabled="busy" @submit.prevent="submit">
      <el-form-item label="主知识点"><el-select v-model="form.kp" filterable placeholder="选择知识点">
        <el-option v-for="p in props.points" :key="p.kp_id" :label="p.name" :value="p.kp_id" /></el-select></el-form-item>
      <el-form-item label="题型"><el-radio-group v-model="form.type" @change="form.keys = []">
        <el-radio-button value="single_choice">单选</el-radio-button><el-radio-button value="multi_choice">多选</el-radio-button>
        <el-radio-button value="true_false">判断</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="题干"><el-input v-model="form.stem" type="textarea" :rows="3"/></el-form-item>
        <template v-if="isChoice">
          <el-form-item v-for="o in form.options" :key="o.key" :label="'选项 ' + o.key"><el-input v-model="o.text"/></el-form-item>
          <el-button :disabled="form.options.length >= 8" @click="addOption">添加选项</el-button>
          <el-button :disabled="form.options.length <= 2" @click="form.keys = form.keys.filter(k => k !== form.options.at(-1)?.key); form.options.pop()">移除末项</el-button>
          <el-form-item label="正确选项"><el-checkbox-group v-if="form.type === 'multi_choice'" v-model="form.keys"><el-checkbox v-for="o in form.options" :key="o.key" :value="o.key">{{ o.key }}</el-checkbox></el-checkbox-group>
            <el-radio-group v-else :model-value="form.keys[0]" @change="(k: unknown) => form.keys = [String(k)]"><el-radio v-for="o in form.options" :key="o.key" :value="o.key">{{ o.key }}</el-radio></el-radio-group></el-form-item>
        </template>
        <el-form-item v-else label="标准答案"><el-radio-group v-model="form.truth"><el-radio :value="true">正确</el-radio><el-radio :value="false">错误</el-radio></el-radio-group></el-form-item>
        <el-form-item label="答案解析"><el-input v-model="form.explanation" type="textarea"/></el-form-item>
        <el-form-item label="关联知识点标签"><el-select v-model="form.tags" multiple filterable><el-option v-for="p in props.points.filter(p => p.kp_id !== form.kp)" :key="p.kp_id" :label="p.name" :value="p.kp_id"/></el-select></el-form-item>
        <el-form-item label="难度"><el-radio-group v-model="form.difficulty"><el-radio v-for="level in ['基础','中等','困难']" :key="level" :value="level">{{ level }}</el-radio></el-radio-group></el-form-item>
        <el-form-item label="分值"><el-input-number v-model="form.score" :min="0.1" :max="1000"/></el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    </el-form>
    <template #footer><el-button :disabled="busy" @click="open = false">取消</el-button><el-button type="primary" :loading="busy" @click="submit">保存题目</el-button></template>
  </el-dialog>
</template>

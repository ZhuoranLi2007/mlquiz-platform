<script setup lang="ts">
import { computed, ref } from 'vue'
import { roundLabel, loopTime, sourceLabel } from '../utils/learningLoop'
const props=defineProps<{ result:any; running?:boolean }>()
const emit=defineEmits<{ retest:[]; openAttempt:[string] }>()
const show=ref(false)
const filter=ref('all')
const page=ref(1)
const loop=computed(()=>props.result?.learning_loop)
const aggregate=computed(()=>props.result?.learning_loop_summary)
const rows=computed(()=>aggregate.value?.rows || (loop.value?[{...loop.value,student_code:'当前学生',source_title:props.result?.assessment?.title}]:[]))
function bucket(row:any) {return ['published','opened'].includes(row.task_state)?'pending':row.task_state==='scoring_pending'?'scoring_pending':row.round_result?.status || 'practice'}
const historicalPassed=computed(()=>new Set(rows.value.filter((r:any)=>r.last_passed).map((r:any)=>r.student_id)).size)
const filtered=computed(()=>rows.value.filter((r:any)=>filter.value==='all'||bucket(r)===filter.value))
const visible=computed(()=>filtered.value.slice((page.value-1)*6,page.value*6))
</script>
<template>
  <section class="teacher-loop" aria-label="教师复测操作区">
    <div class="loop-caption">
      <b v-if="loop" :class="{passed:loop.round_result?.status==='passed'}">{{ roundLabel(loop.round_result) }}</b>
      <b v-else>复测进度</b>
      <span v-if="aggregate">当前任务：通过 {{ aggregate.counts.passed }} · 待完成 {{ aggregate.counts.pending }} · 待评分 {{ aggregate.counts.scoring_pending }} · 未通过 {{ aggregate.counts.not_passed }}</span>
      <span v-if="aggregate && historicalPassed" class="passed">{{ historicalPassed }} 人有通过记录</span>
      <span v-if="loop?.round_result">第 {{ loop.round_result.round }} 轮 · {{ loop.round_result.correct_count }}/{{ loop.round_result.question_count }} · {{ loopTime(loop.round_result.completed_at) }}</span>
      <span v-if="loop?.concept_results?.length">知识点达标 {{ loop.concept_results.filter((c:any)=>['achieved','delayed_confirmed'].includes(c.state)).length }}/{{ loop.concept_results.length }}</span>
      <span v-if="loop?.last_passed && loop.last_passed.cycle_id!==loop.round_result?.cycle_id" class="passed">第 {{ loop.last_passed.round }} 轮曾通过</span>
      <span v-if="running" role="status">准备中，AI 不可用时自动使用题库</span>
    </div>
    <div class="actions"><el-button @click="show=true">查看进度与结果</el-button><el-button type="primary" :loading="running" @click="emit('retest')">{{ loop?.task_state==='scoring_pending'?'安排巩固练习':loop?.round_result?'再次安排复测':'安排复测' }}</el-button></div>
    <el-drawer v-model="show" title="复测进度与学习证据" size="min(680px, 96vw)">
      <el-radio-group v-model="filter" @change="page=1"><el-radio-button value="all">全部</el-radio-button><el-radio-button value="pending">待完成</el-radio-button><el-radio-button value="scoring_pending">待评分</el-radio-button><el-radio-button value="passed">通过</el-radio-button><el-radio-button value="not_passed">未通过</el-radio-button></el-radio-group>
      <el-empty v-if="!filtered.length" description="当前筛选没有复测记录，可随时安排新复测" />
      <article v-for="row in visible" :key="`${row.student_id}:${row.source_attempt_id}`" class="progress-row">
        <header><b>{{ row.student_code }} · {{ row.source_title }}</b><el-tag :type="row.round_result?.status==='passed'?'success':'info'">{{ roundLabel(row.round_result) }}</el-tag></header>
        <p v-if="row.task_state==='scoring_pending'">新任务待评分，已有成果保留。</p><p v-else-if="['published','opened'].includes(row.task_state)">第 {{ row.latest_task?.round }} 轮等待学生完成。</p>
        <p v-if="row.round_result">第 {{ row.round_result.round }} 轮 · {{ row.round_result.correct_count }}/{{ row.round_result.question_count }} · {{ loopTime(row.round_result.completed_at) }}</p>
        <p v-if="row.latest_task?.source_counts">第 {{ row.latest_task.round }} 轮题源：{{ sourceLabel(row.latest_task.source_counts) }}</p>
        <div class="concept-tags"><el-tag v-for="concept in row.concept_results" :key="concept.kp_id" :type="['achieved','delayed_confirmed'].includes(concept.state)?'success':'info'">{{ concept.name || concept.kp_id }} · {{ concept.label }}</el-tag></div>
        <el-button v-if="row.round_result?.retest_attempt_id" @click="emit('openAttempt',row.round_result.retest_attempt_id)">查看答卷</el-button>
      </article>
      <el-pagination v-if="filtered.length>6" v-model:current-page="page" :page-size="6" :total="filtered.length" layout="prev, pager, next" />
    </el-drawer>
  </section>
</template>
<style scoped>
.teacher-loop{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;border:1px solid #d8e5f4;border-radius:10px;background:#f4f8ff}.loop-caption{display:flex;gap:5px 12px;align-items:baseline;flex-wrap:wrap;min-width:0}.loop-caption b{color:#30496c}.loop-caption span{font-size:13px;color:#61738b}.loop-caption .passed{color:#158153}.actions{display:flex;gap:8px;flex-shrink:0}.actions .el-button{margin:0}.progress-row{padding:14px 0;border-bottom:1px solid #e4eaf3}.progress-row header{display:flex;align-items:center;justify-content:space-between;gap:12px}.progress-row p{font-size:14px;color:#5e7087}.concept-tags{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}@media(max-width:700px){.teacher-loop{flex-wrap:wrap}.actions{margin-left:auto}}
</style>

<style scoped>.progress-row{padding:16px;border:1px solid #dce7f5;border-radius:12px;margin-top:14px;background:#fff}.progress-row header{align-items:start}.progress-row header>b{max-width:75%;font-size:15px;line-height:1.6}.progress-row p{line-height:1.6;margin:9px 0}.concept-tags{gap:6px}.progress-row .el-button{margin-top:4px}.progress-row header .el-tag{flex:none}.progress-row .concept-tags .el-tag{white-space:normal;height:auto;min-height:26px;line-height:1.5}</style>

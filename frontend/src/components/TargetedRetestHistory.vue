<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheck, Clock, RefreshRight, WarningFilled } from '@element-plus/icons-vue'
import AttemptEvidenceTable from './AttemptEvidenceTable.vue'

const props = defineProps<{ history?: any[]; originalSummary?: any }>()
const rows = computed(() => props.history || [])

function percent(value: unknown) {
  return value === null || value === undefined ? '—' : `${Math.round(Number(value) * 100)}%`
}
function statusText(row: any) {
  if (row.status === 'passed') return '本轮已达标'
  if (row.status === 'not_passed') return '本轮未达标'
  if (row.status === 'scoring_pending') return '评分未完成'
  if (row.status === 'opened') return '学生作答中'
  return '待学生完成'
}
function statusIcon(row: any) {
  if (row.status === 'passed') return CircleCheck
  if (row.status === 'not_passed') return WarningFilled
  return Clock
}
function dateText(value: string) {
  return value ? value.slice(0, 19).replace('T', ' ') : '时间未记录'
}
</script>

<template>
  <section class="retest-history">
    <header class="retest-title">
      <div><el-icon><RefreshRight /></el-icon><h2>针对性复测</h2></div>
      <span>{{ rows.length ? `共 ${rows.length} 轮` : '尚未发布' }}</span>
    </header>
    <div v-if="rows.length" class="round-list">
      <details v-for="(row, index) in rows" :key="row.cycle_id" class="round-card" :open="index === rows.length - 1">
        <summary>
          <div class="round-main">
            <el-icon :class="row.status"><component :is="statusIcon(row)" /></el-icon>
            <div><b>第{{ row.round }}轮复测</b><span>{{ dateText(row.submitted_at || row.published_at) }}</span></div>
          </div>
          <div class="round-result">
            <strong v-if="row.result_determinable">{{ row.correct_count }}/{{ row.question_count }}题 · {{ percent(row.accuracy) }}</strong>
            <strong v-else>{{ row.question_count }}题</strong>
            <em :class="row.status">{{ statusText(row) }}</em>
          </div>
        </summary>
        <div class="round-content">
          <div v-if="row.result_determinable" class="comparison-strip">
            <div><span>原卷表现</span><b>{{ originalSummary?.correct_items ?? '—' }}/{{ originalSummary?.scored_items ?? '—' }}题正确</b></div>
            <i>→</i>
            <div><span>第{{ row.round }}轮新题复测</span><b>{{ row.correct_count }}/{{ row.question_count }}题正确</b></div>
            <p>这是原卷表现与新题复测表现的对照；两次题目不同，不能直接解释为同一道题提高了多少。</p>
          </div>
          <div v-else class="waiting-state">
            <el-icon><Clock /></el-icon><div><b>{{ statusText(row) }}</b><span v-if="row.status === 'scoring_pending'">存在未可靠评分题目，本轮暂不执行80%达标判定。</span><span v-else>任务已经发布，学生提交后会在这里显示成绩和错题。</span></div>
          </div>
          <template v-if="row.items?.length">
            <AttemptEvidenceTable :result="{ items: row.items }" section="problems" />
            <AttemptEvidenceTable :result="{ items: row.items }" section="correct" />
          </template>
        </div>
      </details>
    </div>
    <div v-else class="empty-retest"><span>诊断确认复测目标后，可从右侧生成并发布5道针对性新题。</span></div>
  </section>
</template>

<style scoped>
.retest-history{display:grid;gap:10px;min-width:0}.retest-title{display:flex;align-items:center;justify-content:space-between;margin-top:3px}.retest-title>div{display:flex;align-items:center;gap:8px}.retest-title .el-icon{color:#315cf0;font-size:20px}.retest-title h2{margin:0;color:#17243d;font-size:18px}.retest-title>span{color:#7b899b;font-size:11px}.round-list{display:grid;gap:10px}.round-card{overflow:hidden;border:1px solid #dce5f2;border-radius:13px;background:#fff}.round-card>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 15px;cursor:pointer;background:#f8faff}.round-main,.round-result{display:flex;align-items:center;gap:10px}.round-main>.el-icon{display:grid;width:30px;height:30px;place-items:center;border-radius:8px;background:#eef3fb;color:#5e7190}.round-main>.el-icon.passed{background:#e8f7ef;color:#168058}.round-main>.el-icon.not_passed{background:#fff0f1;color:#d3424d}.round-main b,.round-main span{display:block}.round-main b{color:#263950;font-size:12px}.round-main span{margin-top:2px;color:#8290a2;font-size:9.5px}.round-result strong{color:#263950;font-size:12px}.round-result em{padding:4px 7px;border-radius:999px;background:#eef3fb;color:#5d6d83;font-size:10px;font-style:normal}.round-result em.passed{background:#e7f7ee;color:#14794f}.round-result em.not_passed{background:#ffedef;color:#c83b46}.round-content{display:grid;gap:12px;padding:13px}.comparison-strip{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;padding:12px;border-radius:10px;background:#f5f8fd}.comparison-strip div{text-align:center}.comparison-strip span,.comparison-strip b{display:block}.comparison-strip span{color:#76859a;font-size:10px}.comparison-strip b{margin-top:4px;color:#273b58;font-size:14px}.comparison-strip i{color:#6c7e99;font-style:normal}.comparison-strip p{grid-column:1/-1;margin:0;color:#7c899a;font-size:9.5px;text-align:center}.waiting-state{display:flex;align-items:center;gap:11px;padding:15px;border-radius:10px;background:#f5f8fd}.waiting-state>.el-icon{color:#5073d8;font-size:23px}.waiting-state b,.waiting-state span{display:block}.waiting-state b{color:#30435f}.waiting-state span{margin-top:3px;color:#748197;font-size:10.5px}.empty-retest{padding:18px;border:1px dashed #cbd8e8;border-radius:12px;background:#fbfcfe;color:#7c899a;font-size:11px;text-align:center}@media(max-width:700px){.round-card>summary{align-items:flex-start;flex-direction:column}.round-result{width:100%;justify-content:space-between}.comparison-strip{grid-template-columns:1fr}.comparison-strip i{display:none}}
</style>

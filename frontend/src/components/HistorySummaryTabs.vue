<script setup lang="ts">
import { scoreRateLabel } from '../utils/historyDiagnosis'

defineProps<{
  stats: Record<string, any>
  activeTab: 'overview' | 'wrong' | 'records'
}>()
const emit = defineEmits<{ 'update:activeTab': [value: 'overview' | 'wrong' | 'records'] }>()
</script>

<template>
  <div class="history-shared-head">
    <section class="history-key-figures" aria-label="多卷诊断统计">
      <div><strong>{{ scoreRateLabel(stats.average_score_rate) }}</strong><span>平均得分率</span></div>
      <div><strong>{{ stats.participated_assessments ?? 0 }}<small> 次</small></strong><span>参加考试</span></div>
      <div><strong>{{ stats.unique_wrong_question_count ?? 0 }}<small> 道</small></strong><span>不同错题</span></div>
      <p>已评分 {{ stats.scored_item_events ?? 0 }} 题次 <span>·</span> 共错 {{ stats.wrong_item_events ?? 0 }} 次</p>
    </section>
    <nav class="history-main-tabs" role="tablist" aria-label="单人多卷诊断内容">
      <button v-for="tab in [
        { id: 'overview', label: '诊断概览' },
        { id: 'wrong', label: `错题 ${stats.unique_wrong_question_count ?? 0}` },
        { id: 'records', label: `考试记录 ${stats.participated_assessments ?? 0}` },
      ] as const" :key="tab.id" type="button" role="tab" :aria-selected="activeTab === tab.id"
        :class="{ active: activeTab === tab.id }" @click="emit('update:activeTab', tab.id)">{{ tab.label }}</button>
    </nav>
  </div>
</template>

<style scoped src="../styles/historySummaryTabs.css"></style>

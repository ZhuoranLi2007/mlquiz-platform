<script setup lang="ts">
import { loopTime } from '../utils/learningLoop'
import { computed, ref } from 'vue'
const props = defineProps<{ concept: any }>()
const newestFirst = ref(true)
const events = computed(() => {
  const rows = props.concept.timeline || []
  const dated = rows.filter((event: any) => event.at && !Number.isNaN(Date.parse(event.at)))
  const undated = rows.filter((event: any) => !event.at || Number.isNaN(Date.parse(event.at)))
  return (newestFirst.value ? dated.reverse() : dated).concat(undated)
})
const emit = defineEmits<{ answers: []; retest: [round: number] }>()
const labels: Record<string, string> = {
  needs_review: '需加强', achieved: '本轮证据达标', insufficient: '证据不足',
  pending: '等待作答', awaiting_score: '等待评分', not_covered: '未覆盖此知识点',
  correct_this_round: '本轮相关题答对', recorded: '已记录',
}
</script>
<template>
  <section class="learning-timeline" aria-label="学习证据时间线">
    <div class="timeline-order"><button type="button" :aria-pressed="newestFirst" @click="newestFirst = !newestFirst">{{ newestFirst ? '最近记录在前' : '按发生顺序' }} ⇅</button></div>
    <p class="timeline-rule">{{ concept.success_check }}</p>
    <ol v-if="concept.timeline?.length">
      <li v-for="event in events" :key="event.id" :class="event.state">
        <div class="event-heading"><b>{{ event.title }}</b><time>{{ loopTime(event.at) || '时间未记录' }}</time></div>
        <span class="event-state">{{ labels[event.state] || '证据待确认' }}</span>
        <p>{{ event.description }}</p>
        <p v-if="event.scored_count > 0">已评分题答对 {{ event.correct_count }}/{{ event.scored_count }} 题<span v-if="event.ungraded_count"> · {{ event.ungraded_count }} 题待评分</span></p>
        <p v-else-if="event.ungraded_count">尚无已评分题目 · {{ event.ungraded_count }} 题待评分</p>
        <p v-if="event.kind === 'retest' && event.scored_count" class="event-proof">{{ event.independent ? '独立作答证据' : '独立性证据不足' }}<template v-if="event.distinct_families != null"> · {{ event.distinct_families }} 个题族 · {{ event.distinct_angles }} 个考查角度</template><template v-else> · 题族与角度未记录</template></p>
        <el-button v-if="event.action === 'answers'" size="small" @click="emit('answers')">查看作答依据</el-button>
        <el-button v-if="event.action === 'retest'" size="small" @click="emit('retest', event.round)">查看本轮任务</el-button>
      </li>
    </ol>
    <el-empty v-else description="暂无可追溯的学习记录"/>
    <p class="timeline-note">查看与阅读完成按实际操作记录，知识点状态由作答和复测证据更新。</p>
  </section>
</template>
<style scoped>
.timeline-order{display:flex;justify-content:flex-end}.timeline-order button{border:0;background:#f2f6fc;border-radius:5px;padding:5px 8px;font:inherit;font-size:12px;color:#557198;cursor:pointer}.timeline-order button:focus-visible{outline:2px solid #315fc0}
.timeline-rule,.timeline-note{font-size:13px;color:#6a7d94;line-height:1.7}.learning-timeline ol{list-style:none;margin:18px 0;padding:0 0 0 17px}.learning-timeline li{position:relative;padding:0 0 24px 22px;border-left:2px solid #dce6f5}.learning-timeline li:last-child{border-color:transparent}.learning-timeline li::before{content:'';position:absolute;left:-7px;top:3px;width:12px;height:12px;border:3px solid #fff;box-shadow:0 0 0 1px #9fb3ce;background:#8199b7;border-radius:50%;box-sizing:border-box}.learning-timeline li.achieved::before{background:#218568;box-shadow:0 0 0 1px #218568}.learning-timeline li.needs_review::before{background:#b96b20;box-shadow:0 0 0 1px #b96b20}.event-heading{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.event-heading b{font-size:15px;color:#2c425d}.event-heading time{font-size:12px;color:#74859b}.event-state{display:inline-block;font-size:12px;margin:6px 0;color:#617995}.achieved .event-state{color:#218568}.needs_review .event-state{color:#a46024}.learning-timeline p{font-size:14px;line-height:1.65;color:#536a84}.event-proof{color:#657b98!important}.learning-timeline .el-button{margin-top:7px}
</style>

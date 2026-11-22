<script setup lang="ts">
defineProps<{ path: any; activeTask?: any }>()
const emit = defineEmits<{ study: []; test: []; evidence: [] }>()
</script>
<template>
  <section v-if="path" class="adaptive-path" aria-label="个性化学习路径">
    <header><span>{{ path.label }}</span><b>{{ path.difficulty }}难度 · {{ path.cognitive_level }}层次</b></header>
    <p class="path-reason">{{ path.reason }}</p>
    <p v-if="path.training_scope" class="path-note">{{ path.training_scope }}</p>
    <p v-if="activeTask?.retest_kind === 'practice'" class="path-note">当前任务为巩固练习。完成后可继续准备独立新题进行验证。</p>
    <ol>
      <li v-for="step in path.steps" :key="step.key" :class="{complete:step.complete}">
        <span class="step-dot" aria-hidden="true">{{ step.complete ? '✓' : '○' }}</span>
        <div><h3>{{ step.label }}<small v-if="step.complete">已记录</small></h3><p>{{ step.detail }}</p>
          <el-button v-if="step.key === 'study'" size="small" @click="emit('study')">查看对应资料</el-button>
          <el-button v-if="step.key === 'test'" type="primary" size="small" @click="emit('test')">{{ activeTask ? '继续当前复测' : '准备针对性训练' }}</el-button>
          <el-button v-if="step.key === 'confirm'" size="small" @click="emit('evidence')">查看证据时间线</el-button>
        </div>
      </li>
    </ol>
    <p class="path-note">题目按学习阶段、未见题和考查角度优先选择。题库不足时明确提供巩固练习，实际难度与题源随任务保存。</p>
  </section>
</template>
<style scoped>
.adaptive-path {
  font-size:14px;
  color:#516780
}
.adaptive-path>header {
  display:flex;
  align-items:center;
  gap:10px;
  flex-wrap:wrap;
  margin-top:12px
}
.adaptive-path>header>span {
  background:#eaf1ff;
  color:#275cba;
  border-radius:6px;
  padding:5px 9px;
  font-weight:650
}
.adaptive-path>header>b {
  font-size:13px;
  font-weight:500;
  color:#6c8099
}
.path-reason {
  padding-bottom:8px;
  border-bottom:1px solid #e5edf6
}
.adaptive-path ol {
  list-style:none;
  margin:18px 0;
  padding:0
}
.adaptive-path li {
  display:flex;
  gap:12px;
  padding:0 0 22px;
  position:relative
}
.step-dot {
  flex:none;
  width:24px;
  height:24px;
  display:grid;
  place-items:center;
  border-radius:50%;
  background:#eff4fb;
  color:#7390b6
}
.complete .step-dot {
  background:#e5f6ec;
  color:#26815f
}
.adaptive-path h3 {
  margin:1px 0 7px;
  font-size:15px;
  color:#2c435f
}
.adaptive-path h3 small {
  margin-left:8px;
  font-size:12px;
  font-weight:400;
  color:#26815f
}
.adaptive-path p {
  margin:8px 0;
  line-height:1.7
}
.path-note {
  padding:10px;
  background:#f5f8fc;
  border-radius:8px;
  font-size:12px;
  color:#73849a
}

</style>

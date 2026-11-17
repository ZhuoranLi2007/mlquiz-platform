<script setup lang="ts">
import { computed, ref } from 'vue'
import { recommendedResourceLinks } from '../utils/resourceLinks'
const props = defineProps<{ resources?: any[] }>()
const expanded = ref(false)
const all = computed(() => recommendedResourceLinks(props.resources || [], [], 100))
const rows = computed(() => expanded.value ? all.value : all.value.slice(0, 6))
function type(row:any) {
  if (row.source_type === 'local') return '课程原页 · 可离线阅读'
  return ({ bilibili:'B站', douyin:'抖音', csdn:'CSDN文章' } as Record<string,string>)[row.platform] || row.platform || row.type || '学习资料'
}
</script>
<template>
  <div v-if="rows.length" class="learning-resource-list">
    <a v-for="row in rows" :key="row.url" :href="row.url" target="_blank" rel="noopener noreferrer" class="diagnosis-resource-link">
      <small>{{ row.kp_name ? `${row.kp_name} · ` : '' }}{{ type(row) }}</small>
      <b class="resource-title">{{ row.title }}</b>
      <span v-if="row.location" class="resource-location">看这里：{{ row.location }}</span>
      <span v-if="row.study_focus || row.reason">{{ row.study_focus || row.reason }}</span>
      <strong class="open-resource">{{ row.source_type === 'local' ? '打开课程精读 →' : '打开对应内容 →' }}</strong>
    </a>
    <button v-if="all.length > 6" type="button" @click="expanded=!expanded">{{ expanded ? '收起更多资料' : `查看其他 ${all.length - 6} 项资料` }}</button>
  </div>
  <p v-else class="no-target-resource">完成有效评分并确定复习知识点后，会在这里提供对应资料。</p>
</template>
<style scoped>
.learning-resource-list{display:grid;gap:10px;min-width:0}.diagnosis-resource-link{display:grid;gap:5px;min-width:0;padding:10px;border:1px solid #e1e7ef;border-radius:6px;color:#43546c;text-decoration:none;line-height:1.55}
.diagnosis-resource-link:hover{border-color:#91a7c6}.resource-title{font-size:14px;color:#263950;overflow-wrap:anywhere}.diagnosis-resource-link small,.diagnosis-resource-link span{font-size:12px;overflow-wrap:anywhere}
.diagnosis-resource-link small{color:#64748b}.resource-location{color:#44618b}.open-resource{color:#315fc0;font-size:12px;font-weight:500}.no-target-resource{color:#64748b;font-size:13px;line-height:1.6}.learning-resource-list button{border:0;background:none;color:#315fc0;text-align:left;cursor:pointer}
</style>

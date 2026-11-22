<script setup lang="ts">
import { computed, ref } from 'vue'
import { chapterGroups, knowledgeLabel, knowledgeState } from '../utils/knowledgeMap'

const props = defineProps<{ concepts: any[]; selectedId?: string }>()
const emit = defineEmits<{ select: [id: string] }>()
const query = ref('')
const filter = ref('')
const zoom = ref(100)
const groups = computed(() => chapterGroups(props.concepts, query.value, filter.value))
const states = [
  { key: '', label: '全部' }, { key: 'needs_review', label: '需加强' },
  { key: 'insufficient', label: '证据不足' }, { key: 'achieved', label: '已达标' },
  { key: 'delayed_confirmed', label: '延后确认' },
]
</script>
<template>
  <section class="knowledge-map" aria-label="知识点掌握地图">
    <div class="map-tools">
      <el-input v-model="query" clearable placeholder="搜索知识点或章节" aria-label="搜索知识点或章节"/>
      <el-button-group>
        <el-button aria-label="缩小地图节点" :disabled="zoom <= 80" @click="zoom -= 10">−</el-button>
        <el-button aria-label="重置地图节点大小" @click="zoom = 100">{{ zoom }}%</el-button>
        <el-button aria-label="放大地图节点" :disabled="zoom >= 140" @click="zoom += 10">＋</el-button>
      </el-button-group>
    </div>
    <div class="map-filters" aria-label="按掌握状态筛选">
      <button v-for="state in states" :key="state.key" type="button" :aria-pressed="filter === state.key" @click="filter = state.key">{{ state.label }}</button>
    </div>
    <p class="map-scope">按章节组织 · 当前试卷与关联复测证据</p>
    <div class="map-chapters" :style="{ '--map-size': zoom / 100 }">
      <section v-for="group in groups" :key="group.chapter" class="chapter-cluster">
        <h3>{{ group.chapter }}</h3>
        <div class="chapter-nodes">
          <button v-for="concept in group.items" :key="concept.id" type="button"
            class="knowledge-node" :class="[knowledgeState(concept), { selected: selectedId === concept.id }]"
            :aria-pressed="selectedId === concept.id" @click="emit('select', concept.id)">
            <b>{{ concept.name }}</b>
            <span class="node-state"><i aria-hidden="true"/>{{ knowledgeLabel(concept) }}</span>
            <small>原卷 {{ concept.statistics.scored_count }} 题已评分<span v-if="concept.statistics.ungraded_count"> · {{ concept.statistics.ungraded_count }} 题待评分</span></small>
          </button>
        </div>
      </section>
      <el-empty v-if="!groups.length" :description="concepts.length ? '没有符合筛选条件的知识点' : '暂无知识点证据'"/>
    </div>
  </section>
</template>
<style scoped>
.knowledge-map{min-width:0}.map-tools{display:flex;flex-wrap:wrap;gap:8px}.map-tools .el-input{flex:1;min-width:130px}.map-tools .el-button{padding:8px}
.map-filters{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}.map-filters button{border:1px solid #dce5f2;background:#fff;color:#53677f;border-radius:20px;padding:5px 9px;font:inherit;font-size:13px;cursor:pointer}
.map-filters button[aria-pressed=true]{background:#edf3ff;border-color:#8da9df;color:#245bb9}.map-scope{color:#738196;font-size:12px!important;margin:10px 0!important}
.map-chapters{display:grid;gap:20px}.chapter-cluster{border-left:2px solid #dce6f5;padding-left:12px}.chapter-cluster h3{margin:0 0 10px;color:#596e88;font-size:14px;line-height:1.5}.chapter-nodes{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,145px),1fr));gap:10px}
.knowledge-node{--state:#738196;display:flex;flex-direction:column;align-items:flex-start;gap:8px;min-width:0;position:relative;padding:calc(13px * var(--map-size));border:1px solid #dce5f2;border-top:3px solid var(--state);border-radius:10px;background:#fbfcff;color:#2d415d;text-align:left;cursor:pointer;transition:box-shadow .15s,border-color .15s}
.knowledge-node.needs_review{--state:#b96b20;background:#fffaf3}.knowledge-node.achieved{--state:#218568;background:#f4fbf7}.knowledge-node.delayed_confirmed{--state:#4569b7;background:#f3f6ff}
.knowledge-node:hover,.knowledge-node.selected{box-shadow:0 0 0 2px #bdd0ef;border-color:#7c9fcf}.knowledge-node b{font-size:calc(14px * var(--map-size));line-height:1.5;overflow-wrap:anywhere}.knowledge-node small{font-size:12px;line-height:1.5;color:#697b91}.node-state{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--state)}.node-state i{width:6px;height:6px;border-radius:50%;background:var(--state)}
button:focus-visible{outline:2px solid #315fc0;outline-offset:3px}
</style>

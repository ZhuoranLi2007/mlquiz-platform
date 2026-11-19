<script setup lang="ts">
import WorkbenchPage from "../components/WorkbenchPage.vue"
import { auth } from "../auth"
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
const route = useRoute()
const router = useRouter()
const selectedPage = ref<number>()
function jumpPage(page:number) { document.getElementById(`course-page-${page}`)?.scrollIntoView({ block: "start" }) }
const material = ref<any>(null)
const error = ref('')
onMounted(async () => {
  try { material.value = await api.get(`/learning-materials/${encodeURIComponent(String(route.params.kpId))}`) }
  catch { error.value = '课程材料加载失败，请刷新重试。' }
})
</script>
<template>
  <WorkbenchPage class="learning-material" label="课程原页阅读区">
    <template #header>
    <header><span>课程精读</span><h1>{{ material?.name || '学习材料' }}</h1></header>
    <div class="workbench-toolbar"><el-button @click="router.push(auth.isTeacher.value ? '/teacher/resources' : '/student/online')">返回资源列表</el-button><el-select v-if="material" v-model="selectedPage" aria-label="跳转课程页码" placeholder="跳转原文页码" @change="jumpPage"><el-option v-for="page in material.pages" :key="page.page" :label="`第 ${page.page} 页 · ${page.title}`" :value="page.page" /></el-select></div>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else-if="!material">正在加载课程原页…</p>
    <template v-else>
      <section class="learning-task"><h2>这次重点看什么</h2><p>{{ material.action }}</p>
        <p><b>自测标准：</b>{{ material.success_check }}</p>

      </section>
      <section v-if="material.reading_note" class="learning-task"><h2>阅读时核对这一点</h2>
        <p>{{ material.reading_note }}</p><a v-if="material.note_source" :href="material.note_source" target="_blank" rel="noopener noreferrer">查看公式依据</a>
      </section>
      <article :id="`course-page-${page.page}`" v-for="page in material.pages" :key="page.page" class="source-page">
        <h2>{{ page.title }} · 第 {{ page.page }} 页</h2>
        <a v-if="page.image_url" :href="page.image_url" target="_blank" rel="noopener noreferrer" title="打开原图查看公式">
          <img :src="page.image_url" :alt="`${page.title} 第${page.page}页`" loading="lazy" />
        </a>
        <p v-else>原页图暂不可用，以下保留已提取文字。</p>
        <details><summary>查看可检索文字（公式以原页为准）</summary><pre>{{ page.excerpt }}</pre></details>
      </article>
      <footer>完成后回到原诊断页面，对照错题解释判断依据，随时发起下一轮复测。</footer>
    </template>
  </WorkbenchPage>
</template>
<style scoped>
.learning-material{max-width:1080px;margin:0 auto;padding:30px 22px;color:#263950;background:#fff;min-height:100vh}
header span,small,footer{color:#64748b}h1{font-size:26px;margin:8px 0 22px}h2{font-size:18px;margin:0 0 12px}
.learning-task{background:#f4f7fb;border:1px solid #e0e7ef;padding:18px;margin-bottom:24px;line-height:1.7}
.learning-task p{margin:6px 0}.source-page{margin:25px 0}.source-page img{width:100%;height:auto;border:1px solid #dce3ed}
details{margin-top:10px}summary{cursor:pointer}pre{font-family:inherit;white-space:pre-wrap;line-height:1.7}footer{padding:20px 0;line-height:1.6}
</style>

<style scoped>
.learning-material.workbench-page{height:100dvh;min-height:0;max-width:1200px;padding:18px 22px;display:flex}.learning-material h1{margin:4px 0;font-size:26px}.source-page{scroll-margin-top:12px}.learning-task{margin-bottom:18px}.learning-material :deep(.workbench-page-body){padding-right:8px}
</style>

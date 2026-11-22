<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { VideoPlay } from '@element-plus/icons-vue'
import { api } from '../api/client'
import { auth } from '../auth'
import { ElMessage } from 'element-plus'
import { resourceHref } from '../utils/resourceLinks'
const props=defineProps<{modelValue:boolean;resource:any}>()
const emit=defineEmits<{ 'update:modelValue':[boolean] }>()
const shown=computed({get:()=>props.modelValue,set:v=>emit('update:modelValue',v)})
const material=ref<any>(null),loading=ref(false),pageIndex=ref(0),topic=ref(''),coverBroken=ref(false)
let sequence=0
const title=computed(()=>props.resource?.title_zh||props.resource?.title||'学习资料')
const isVideo=computed(()=>['bilibili','douyin'].includes(props.resource?.platform)||props.resource?.content_type==='video')
const topics=computed(()=>props.resource?.kp_ids?.length?props.resource.kp_ids:props.resource?.kp_id?[props.resource.kp_id]:String(props.resource?.url||'').startsWith('/learn/')?[decodeURIComponent(props.resource.url.split('/learn/')[1].split('?')[0])]:[])
const page=computed(()=>material.value?.pages?.[pageIndex.value])
const href=computed(()=>resourceHref(props.resource?.url))
const bookmarkUrl = computed(() => props.modelValue && auth.isStudent.value && props.resource?.resource_id
  ? '/student/resource-bookmarks/' + encodeURIComponent(props.resource.resource_id) : '')
const bookmarked = ref<boolean | null>(null)
const saving = ref(false)
watch(bookmarkUrl, async (url, _, onCleanup) => {
  let current = true
  onCleanup(() => { current = false })
  bookmarked.value = null
  if (!url) return
  try {
    const result = await api.get(url)
    if (current) bookmarked.value = result.saved
  } catch { if (current) ElMessage.warning('未能读取收藏状态，请重新打开预览') }
}, { immediate: true })
async function toggleBookmark() {
  const url = bookmarkUrl.value
  if (!url || saving.value || bookmarked.value === null) return
  saving.value = true
  try {
    const result = await api.put(url, { saved: !bookmarked.value })
    if (url === bookmarkUrl.value) bookmarked.value = result.saved
  } catch { ElMessage.error('收藏更新失败，请重试') }
  finally { saving.value = false }
}
async function loadTopic(){const id=++sequence;material.value=null;pageIndex.value=0;if(!topic.value)return;loading.value=true;try{const r=await api.get('/learning-materials/'+encodeURIComponent(topic.value));if(id===sequence)material.value=r}catch{/* The original link remains available when course pages are absent. */}finally{if(id===sequence)loading.value=false}}
watch(()=>[props.modelValue,props.resource],()=>{coverBroken.value=false;if(props.modelValue){topic.value=topics.value[0]||'';void loadTopic()}else{sequence++;loading.value=false}},{immediate:true})
</script>
<template>
 <el-dialog v-model="shown" title="资源预览" width="min(1120px, 95vw)" top="4vh" class="resource-preview-dialog" destroy-on-close>
  <div class="resource-preview-layout">
   <aside class="resource-info">
    <span class="source-label">{{ resource?.platform_label || resource?.platform || (resource?.source_type==='local'?'课程资料':'学习资源') }}</span>
    <h2>{{ title }}</h2>
    <div v-if="isVideo && page" class="video-cover" aria-label="视频封面预览"><img v-if="resource?.thumbnail&&!coverBroken" :src="resource.thumbnail" :alt="`${title} 视频封面`" @error="coverBroken=true" /><span v-else><el-icon><VideoPlay /></el-icon>{{ resource?.platform_label || '视频' }}</span></div>
    <p v-if="resource?.author" class="author">{{ resource.author }}</p>
    <div class="topic-list"><span v-for="name in resource?.kp_names || [resource?.kp_name].filter(Boolean)" :key="name">{{ name }}</span></div>
    <section v-if="resource?.intro || resource?.study_focus || resource?.reason"><h3>{{ resource?.study_focus?'复习重点':'资源简介' }}</h3><p>{{ resource?.study_focus || resource?.intro || resource?.reason }}</p></section>
    <section v-if="material"><h3>学习目标</h3><p>{{ material.action }}</p></section>
    <details v-if="resource?.checked_date || resource?.access_label || material?.reading_note" class="access-details"><summary>来源与访问情况</summary><p v-if="resource?.checked_date">检查时间 {{ resource.checked_date }}</p><p v-if="resource?.access_label">{{ resource.access_label }} · {{ resource.content_label || '正文阅读情况以原站为准' }}</p><p v-if="material?.reading_note">{{ material.reading_note }}</p></details>
    <a v-if="href" class="original-link" :href="href" :target="href.startsWith('/')?undefined:'_blank'" rel="noopener noreferrer">{{ href.startsWith('/')?'打开课程精读':'打开原站内容' }} ↗</a>
   </aside>
   <section v-loading="loading" class="resource-preview-content" aria-label="资源预览内容">
    <header><b>{{ material?'配套课程原页':'资源信息' }}</b><el-select v-if="topics.length>1" v-model="topic" aria-label="切换预览知识点" @change="loadTopic"><el-option v-for="(id,i) in topics" :key="id" :value="id" :label="resource?.kp_names?.[i] || id" /></el-select></header>
    <template v-if="page"><div class="page-toolbar"><span>{{ page.title }} · 第 {{ page.page }} 页</span><nav><el-button :disabled="pageIndex===0" @click="pageIndex--">上一页</el-button><span>{{ pageIndex+1 }}/{{ material.pages.length }}</span><el-button :disabled="pageIndex===material.pages.length-1" @click="pageIndex++">下一页</el-button></nav></div><a class="page-image" :href="page.image_url" target="_blank" rel="noopener noreferrer"><img :src="page.image_url" :alt="`${page.title} 第${page.page}页`" /></a></template>
    <div v-else-if="!loading" class="resource-summary"><div v-if="isVideo" class="video-cover large" aria-label="视频封面预览"><img v-if="resource?.thumbnail&&!coverBroken" :src="resource.thumbnail" :alt="`${title} 视频封面`" @error="coverBroken=true" /><span v-else><el-icon><VideoPlay /></el-icon>{{ resource?.platform_label || '视频' }}</span></div><img v-else-if="resource?.thumbnail" :src="resource.thumbnail" :alt="title" @error="($event.target as HTMLImageElement).hidden=true" /><h3>{{ title }}</h3><p>{{ resource?.intro || resource?.study_focus || '本站登记该资源的标题、知识点与原始链接。' }}</p><p class="original-note">完整内容请在原站阅读</p></div>
   </section>
  </div>
  <template #footer>
    <el-button v-if="bookmarkUrl" :aria-pressed="bookmarked === true" :loading="saving" :disabled="bookmarked === null" @click="toggleBookmark">{{ bookmarked ? '已收藏 · 取消' : '收藏资料' }}</el-button>
    <el-button @click="shown=false">完成预览</el-button>
  </template>
 </el-dialog>
</template>
<style scoped>
.resource-preview-layout{display:grid;grid-template-columns:290px minmax(0,1fr);gap:22px;height:min(565px,calc(92dvh - 142px));min-height:280px}.resource-info{display:flex;flex-direction:column;gap:14px;min-width:0;overflow:auto;padding-right:4px}.source-label{font-size:14px;color:#466c9c}.resource-info h2{font-size:21px;line-height:1.5;margin:0;color:#203a5a}.author{margin:0;color:#6a7c90;font-size:14px}.topic-list{display:flex;gap:6px;flex-wrap:wrap}.topic-list span{padding:4px 8px;border-radius:5px;background:#edf4fd;color:#3f679c;font-size:14px}.resource-info section{border-top:1px solid #e4ecf6;padding-top:14px}.resource-info h3{margin:0 0 8px;font-size:15px;color:#304c70}.resource-info p{font-size:15px;line-height:1.7;margin:0}.resource-info section p,.access-details{color:#5d7087}.access-details{font-size:14px;line-height:1.6}.access-details summary{cursor:pointer}.original-link{display:block;text-align:center;border:1px solid #bfd2ef;border-radius:8px;padding:9px;color:#285ec0;text-decoration:none;margin-top:auto;font-size:15px}.resource-preview-content{display:flex;flex-direction:column;min-width:0;min-height:0;background:#f5f8fd;border:1px solid #dfe8f4;border-radius:12px;overflow:hidden}.resource-preview-content>header{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 16px;background:#edf4fd;color:#304c70;flex:none}.resource-preview-content .el-select{width:210px}.page-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;font-size:14px;flex:none}.page-toolbar>span{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.page-toolbar nav{display:flex;align-items:center;gap:8px;flex:none}.page-toolbar .el-button{padding:7px 10px;margin:0}.page-image{display:flex;align-items:center;justify-content:center;flex:1;min-height:0;padding:12px;background:#fff}.page-image img{max-width:100%;max-height:100%;object-fit:contain}.resource-summary{padding:30px;overflow:auto;line-height:1.7}.resource-summary img{max-width:100%;max-height:240px;object-fit:contain}.resource-summary h3{font-size:20px;color:#294766}.original-note{color:#738298;font-size:14px}@media(max-width:760px){.resource-preview-layout{grid-template-columns:1fr;grid-template-rows:150px minmax(240px,1fr);height:68vh}.resource-info{gap:8px}.resource-info h2{font-size:18px}.resource-info section,.access-details{display:none}.original-link{margin:0;padding:6px}.resource-preview-content .el-select{width:140px}.page-toolbar>span{max-width:40%}}
.video-cover{display:flex;align-items:center;justify-content:center;width:100%;aspect-ratio:16/9;max-height:164px;flex:none;border:1px solid #dbe8f8;border-radius:8px;background:#eef5ff;color:#547fb5;overflow:hidden}.video-cover img{width:100%;height:100%;max-height:none;object-fit:cover}.video-cover span{display:grid;justify-items:center;gap:6px;font-size:14px}.video-cover .el-icon{font-size:28px}.resource-summary .video-cover.large{width:min(100%,480px);max-height:270px;margin:0 auto 20px}.resource-summary .video-cover.large img{max-height:none}
</style>

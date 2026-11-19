<script setup lang="ts">
import { computed,onBeforeUnmount,reactive,ref,watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { auth } from '../auth'
import MathText from './MathText.vue'
import { friendlyError,questionAnswerText,questionRubricText,questionTypeLabel } from '../utils/presentation'
const props=defineProps<{modelValue:boolean;metadata:any}>(),emit=defineEmits<{ 'update:modelValue':[boolean]; changed:[] }>()
const shown=computed({get:()=>props.modelValue,set:v=>emit('update:modelValue',v)}),router=useRouter()
const form=reactive({chapter:'',kp_ids:[] as string[],question_types:['single_choice'],count:3,instruction:''})
const editOpen=ref(false),saving=ref(false),edit=reactive<any>({stem:'',options:[],answer:'',explanation:'',note:''})
function startRevision(){const q=current.value;if(!q)return;edit.stem=q.stem;edit.options=JSON.parse(JSON.stringify(q.options||[]));edit.answer=q.answer_payload?.key??q.answer_payload?.keys??q.answer_payload?.value??q.answer_payload?.numeric;edit.explanation=q.explanation||'';edit.note='';editOpen.value=true}
async function saveRevision(){const q=current.value;if(!q)return;const type=q.question_type,choices=edit.options.map((o:any)=>o.key);const answers=Array.isArray(edit.answer)?edit.answer:[edit.answer];if(!edit.stem.trim()||!edit.explanation.trim()||(['single_choice','multi_choice'].includes(type)&&(!edit.options.length||edit.options.some((o:any)=>!o.text.trim())||!answers.length||answers.some((k:any)=>!choices.includes(k))))){ElMessage.warning('请补齐题干、选项、答案和解析');return}saving.value=true;try{const answer={...q.answer_payload};if(type==='single_choice')answer.key=edit.answer;else if(type==='multi_choice')answer.keys=edit.answer;else if(type==='numeric'&&'numeric' in answer)answer.numeric=Number(edit.answer);else answer.value=edit.answer;await api.post(`/admin/questions/${q.question_id}/revise`,{patch:{stem:edit.stem.trim(),options:edit.options,answer_payload:answer,explanation:edit.explanation.trim(),notes:`${q.notes||''}\n教师内容修订：${edit.note||'已核对题干条件、答案与解析一致。'}`}});await loadQuestions();emit('changed');editOpen.value=false;ElMessage.success('修订已保存为新版本')}catch(e){ElMessage.error(friendlyError(e,'修订未保存'))}finally{saving.value=false}}
const busy=ref(false),error=ref(''),job=ref<any>(null),questions=ref<any[]>([]),selected=ref(''),approving=ref(false),composing=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined,disposed=false
const key=computed(()=>`mlq_ai_question_job:${auth.principal.value?.user_id||''}`)
const topics=computed(()=>(props.metadata?.knowledge_points||[]).filter((k:any)=>!form.chapter||k.chapter===form.chapter))
const current=computed(()=>questions.value.find(q=>q.question_id===selected.value)||questions.value[0])
const approved=computed(()=>questions.value.filter(q=>q.active&&q.review_status==='verified'))
const pipeline=computed(()=>job.value?.result?.result?.pipeline||[])
function source(q:any){return (q?.source_refs||[]).map((r:any)=>`${String(r.rel_path||r.title||'课程资料片段').split(/[/\\]/).pop()}${r.page?` · 第${r.page}页`:''}`).join('；')||'来源待核对'}
async function loadQuestions(){const ids=new Set((job.value?.result?.result?.candidates||[]).map((r:any)=>r.question_id));if(!ids.size){questions.value=[];return}const bank:any=await api.get('/teacher/question-bank');questions.value=bank.items.filter((r:any)=>ids.has(r.question_id));if(!ids.has(selected.value))selected.value=questions.value[0]?.question_id||''}
async function poll(id:string){clearTimeout(timer);try{const r:any=await api.get('/teacher/jobs/'+id);if(disposed)return;job.value=r;busy.value=['queued','running'].includes(r.status);if(busy.value){timer=setTimeout(()=>poll(id),2000)}else{await loadQuestions();if(r.status==='failed'||r.status==='cancelled')error.value=r.error||r.result?.result?.message||'本轮未生成可用候选，请查看运行记录'}}catch(e){busy.value=false;error.value=friendlyError(e,'读取任务失败，可刷新进度恢复')}}
async function generate(){if(!form.kp_ids.length)return;error.value='';questions.value=[];busy.value=true;try{const r:any=await api.post('/teacher/ai-questions/jobs',{kp_ids:form.kp_ids,question_types:form.question_types,count:form.count,instruction:form.instruction});localStorage.setItem(key.value,JSON.stringify({job_id:r.job_id,form}));job.value=r;void poll(r.job_id)}catch(e){busy.value=false;error.value=friendlyError(e,'未能启动AI出题')}}
async function approve(){const q=current.value;if(!q)return;approving.value=true;try{if(!q.chapter_id){const kp=props.metadata.knowledge_points.find((k:any)=>k.kp_id===q.main_kp_id);const chapter=props.metadata.chapters.find((c:any)=>c.value===kp?.chapter&&c.chapter_id);if(!chapter)throw new Error('请先在题库详情确认章节');await api.post(`/admin/questions/${q.question_id}/revise`,{patch:{chapter_id:chapter.chapter_id}})}await api.post(`/teacher/ai-candidates/${q.question_id}/approve`,{review_note:'已逐项核对题干、选项、答案、解析与课程依据。'});await loadQuestions();emit('changed');ElMessage.success('已审核入库')}catch(e){ElMessage.error(friendlyError(e,'审核未完成'))}finally{approving.value=false}}
async function compose(){composing.value=true;try{const r:any=await api.post('/teacher/paper-drafts/compose',{title:'AI新题课堂测验',mode:'manual',selected_items:approved.value.map(q=>({question_id:q.question_id,question_version:q.question_version,score:q.score||2})),requirements:{total_score:approved.value.reduce((n,q)=>n+Number(q.score||2),0)}});shown.value=false;await router.push('/teacher/papers/'+r.paper_id)}catch(e){ElMessage.error(friendlyError(e,'组卷未完成'))}finally{composing.value=false}}
watch(()=>props.modelValue,v=>{if(!v)return;try{const saved=JSON.parse(localStorage.getItem(key.value)||'null');if(saved?.job_id){Object.assign(form,saved.form||{});void poll(saved.job_id)}}catch{localStorage.removeItem(key.value)}})
onBeforeUnmount(()=>{disposed=true;clearTimeout(timer)})
</script>
<template>
  <el-dialog v-model="shown" title="AI出新题" width="min(1200px, 96vw)" top="4vh" class="ai-generation-dialog" :close-on-click-modal="false">
    <div class="ai-workbench">
      <form class="generation-settings" @submit.prevent="generate">
        <label>章节<el-select v-model="form.chapter" clearable placeholder="全部章节" @change="form.kp_ids=[]"><el-option v-for="c in metadata.chapters" :key="c.value" :value="c.value" :label="c.value" /></el-select></label>
        <label>知识点<el-select v-model="form.kp_ids" multiple :multiple-limit="2" filterable placeholder="选择1—2个知识点" aria-label="AI出题知识点"><el-option v-for="kp in topics" :key="kp.kp_id" :value="kp.kp_id" :label="kp.name" /></el-select></label>
        <label>题型<el-select v-model="form.question_types" multiple aria-label="AI出题题型"><el-option v-for="type in ['single_choice','multi_choice','true_false','numeric']" :key="type" :value="type" :label="questionTypeLabel(type)" /></el-select></label>
        <label>数量<el-input-number v-model="form.count" :min="1" :max="5" aria-label="AI出题数量" /></label>
        <label>出题要求<el-input v-model="form.instruction" type="textarea" :rows="4" maxlength="1000" placeholder="可指定应用情境、难度或考查角度" /></label>
        <el-button class="generate-button" native-type="submit" type="primary" :loading="busy" :disabled="!form.kp_ids.length||!form.question_types.length">{{ busy?'正在生成':'生成新题' }}</el-button>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
      </form>
      <section class="generation-results" aria-label="AI出题结果">
        <header><b>{{ busy?'出题进行中':questions.length?`${questions.length} 道候选 · 已入库 ${approved.length} 道`:'候选题' }}</b><el-button v-if="job?.job_id" text @click="poll(job.job_id)">刷新进度</el-button></header>
        <div v-if="busy" class="generation-progress" role="status"><el-icon class="is-loading"><svg viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 9 9" fill="none" stroke="currentColor" stroke-width="2" /></svg></el-icon><h3>{{ job?.status==='queued'?'任务已排队':'正在生成与检查题目' }}</h3></div>
        <div v-else-if="!questions.length" class="generation-empty"><h3>{{ job?'本轮没有可用候选':'选择知识点后生成新题' }}</h3><p v-if="job">{{ job.result?.result?.message||'生成结果未通过校验，请调整要求后重试。' }}</p></div>
        <div v-else class="candidate-layout">
          <nav class="candidate-list" aria-label="候选题列表">
            <button v-for="(q,i) in questions" :key="q.question_id" type="button" :class="{selected:current?.question_id===q.question_id}" :aria-current="current?.question_id===q.question_id?'true':undefined" @click="selected=q.question_id">
              <span class="candidate-list-top"><strong>第 {{ i+1 }} 题</strong><span :class="['candidate-status', q.active&&q.review_status==='verified'?'is-approved':q.review_status==='failed'?'is-failed':'']">{{ q.active&&q.review_status==='verified'?'已入库':q.review_status==='failed'?'校验失败':'待审核' }}</span></span>
              <span class="candidate-summary"><MathText :text="q.stem" /></span>
            </button>
          </nav>
          <article v-if="current" class="candidate-detail">
            <h3><MathText :text="current.stem" /></h3>
            <div v-if="current.options?.length" class="candidate-options"><div v-for="o in current.options" :key="o.key"><b>{{ o.key }}</b><MathText :text="o.text" /></div></div>
            <section class="candidate-answer"><div><b>参考答案</b><MathText :text="questionAnswerText(current)" /></div><div><b>解析</b><MathText :text="current.explanation || '解析待补充'" /></div></section>
            <div class="candidate-meta"><span>{{ questionTypeLabel(current.question_type) }}</span><span>{{ metadata.knowledge_points.find((k:any)=>k.kp_id===current.main_kp_id)?.name }}</span></div>
            <details><summary>评分要点与来源</summary><MathText :text="questionRubricText(current)" /><p>{{ source(current) }}</p></details>
            <div class="candidate-actions"><el-button @click="startRevision">修订内容</el-button><el-button v-if="current.review_status!=='verified'" type="primary" :loading="approving" @click="approve">审核入库</el-button><el-tag v-else type="success">已审核入库</el-tag></div>
          </article>
        </div>
        <details v-if="job&&!busy" class="run-evidence"><summary>运行记录</summary><p>任务 {{ job.job_id }} · {{ job.status }}</p><p v-for="step in pipeline" :key="step.stage">{{ step.stage }} · {{ step.status }}</p><p v-if="job.result?.result?.rejected?.length">未采用 {{ job.result.result.rejected.length }} 道候选</p></details>
      </section>
    </div>
    <template #footer><el-button @click="shown=false">关闭</el-button><el-button type="primary" :disabled="!approved.length" :loading="composing" @click="compose">用已审核题目组卷（{{ approved.length }}）</el-button></template>
  </el-dialog>
  <el-dialog v-model="editOpen" title="修订候选题" width="min(760px, 94vw)" top="4vh" :close-on-click-modal="false"><div class="revision-form"><label>题干<el-input v-model="edit.stem" type="textarea" :rows="4" /></label><div class="revision-options"><label v-for="o in edit.options" :key="o.key">选项 {{ o.key }}<el-input v-model="o.text" type="textarea" :rows="2" /></label></div><label>参考答案<el-select v-if="['single_choice','multi_choice'].includes(current?.question_type)" v-model="edit.answer" :multiple="current?.question_type==='multi_choice'" aria-label="修订参考答案"><el-option v-for="o in edit.options" :key="o.key" :value="o.key" :label="o.key" /></el-select><el-select v-else-if="current?.question_type==='true_false'" v-model="edit.answer"><el-option :value="true" label="正确" /><el-option :value="false" label="错误" /></el-select><el-input-number v-else v-model="edit.answer" /></label><label>解析<el-input v-model="edit.explanation" type="textarea" :rows="3" /></label><label>修订依据<el-input v-model="edit.note" placeholder="记录核对依据" /></label></div><template #footer><el-button @click="editOpen=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveRevision">保存新版本</el-button></template></el-dialog>
</template>
<style scoped>
:global(.ai-generation-dialog){--ai-ink:#1f2937;--ai-body:#334155;--ai-muted:#64748b;--ai-line:#e2e8f0;font-family:"Noto Sans SC","Microsoft YaHei UI","Microsoft YaHei",sans-serif}
:global(.ai-generation-dialog .el-dialog__title){font-size:17px;font-weight:600;color:#1f2937}
:global(.ai-generation-dialog .el-dialog__body){padding-top:13px;padding-bottom:12px;color:#334155}
:global(.ai-generation-dialog .el-dialog__footer){padding-top:14px;border-top:1px solid #e2e8f0}
.ai-workbench{display:grid;grid-template-columns:254px minmax(0,1fr);gap:20px;height:min(570px,calc(92dvh - 140px));min-height:300px;color:var(--ai-body);font-family:"Noto Sans SC","Microsoft YaHei UI","Microsoft YaHei",sans-serif}
.generation-settings{display:flex;flex-direction:column;gap:14px;overflow:auto;min-height:0;padding-right:18px;border-right:1px solid var(--ai-line)}
.generation-settings label,.revision-form label{display:grid;gap:7px;font-size:13px;font-weight:500;color:var(--ai-body)}
.generation-settings :deep(.el-select),.generation-settings :deep(.el-input-number){width:100%}
.generation-settings :deep(.el-select__wrapper),.generation-settings :deep(.el-input__wrapper),.generation-settings :deep(.el-textarea__inner){box-shadow:0 0 0 1px var(--ai-line) inset;color:var(--ai-body);font-size:14px}
.generation-settings :deep(.el-select__placeholder),.generation-settings :deep(.el-input__inner),.generation-settings :deep(.el-textarea__inner){color:var(--ai-body)}
.generation-settings :deep(.el-textarea__inner){min-height:98px!important;line-height:1.6;padding:9px 11px;resize:vertical}
.generate-button{width:100%;min-height:40px;font-weight:500}
.generation-results{min-width:0;min-height:0;display:flex;flex-direction:column;gap:12px}
.generation-results>header{display:flex;align-items:center;justify-content:space-between;flex:none;min-height:34px;color:var(--ai-ink);font-size:14px}
.generation-results>header b{font-weight:600}
.generation-progress,.generation-empty{display:flex;flex:1;flex-direction:column;justify-content:center;align-items:center;border:1px solid var(--ai-line);border-radius:9px;text-align:center;color:var(--ai-muted);padding:24px}
.generation-progress>.el-icon{font-size:28px;color:#285fcf}
.generation-progress h3,.generation-empty h3{font-size:16px;font-weight:500;color:var(--ai-body)}
.generation-empty p{font-size:13px;line-height:1.6}
.candidate-layout{display:grid;grid-template-columns:206px minmax(0,1fr);gap:14px;flex:1;min-height:0}
.candidate-list{display:flex;flex-direction:column;gap:8px;overflow:auto;min-height:0;padding-right:2px}
.candidate-list button{display:grid;gap:8px;width:100%;border:1px solid var(--ai-line);border-radius:8px;padding:11px 12px;background:#fff;text-align:left;cursor:pointer;color:var(--ai-body);font:inherit}
.candidate-list button:hover{border-color:#b8c9e4}
.candidate-list button.selected{border-color:#a9c5ea;background:#f3f7fd}
.candidate-list-top{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:13px;line-height:1.35}
.candidate-list-top strong{font-weight:600;color:var(--ai-ink)}
.candidate-status{color:var(--ai-muted);white-space:nowrap;font-size:12px}
.candidate-status.is-approved{color:#166534}.candidate-status.is-failed{color:#b42318}
.candidate-summary{font-size:13px;line-height:1.55;font-weight:400;color:var(--ai-body);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.candidate-detail{overflow:auto;min-width:0;padding:18px 20px;border:1px solid var(--ai-line);border-radius:9px;color:var(--ai-body);font-size:14px;line-height:1.7;overflow-wrap:anywhere}
.candidate-detail h3{font-size:16px;font-weight:600;line-height:1.68;margin:0 0 17px;color:var(--ai-ink)}
.candidate-detail :deep(.katex){font-size:1em}
.candidate-detail :deep(.katex-display){max-width:100%;overflow-x:auto;overflow-y:hidden;margin:.5em 0}
.candidate-options{display:grid;gap:10px;font-size:14px;line-height:1.65}
.candidate-options>div{display:grid;grid-template-columns:20px minmax(0,1fr);gap:8px}
.candidate-options b{font-weight:600;color:var(--ai-body)}
.candidate-answer{display:grid;gap:13px;padding:16px 0;margin-top:19px;border-top:1px solid var(--ai-line);border-bottom:1px solid var(--ai-line);font-size:14px;line-height:1.75}
.candidate-answer>div{display:grid;grid-template-columns:66px minmax(0,1fr);gap:9px;align-items:start}
.candidate-answer b{font-weight:600;color:var(--ai-ink)}
.candidate-meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;font-size:12px;color:var(--ai-muted)}
.candidate-detail details{font-size:12px;color:var(--ai-muted);margin:11px 0 14px;line-height:1.6}
.candidate-detail summary,.run-evidence summary{cursor:pointer}
.candidate-detail details p{margin:5px 0}
.candidate-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.candidate-actions .el-button+.el-button{margin-left:0}
.run-evidence{flex:none;max-height:100px;overflow:auto;font-size:12px;color:var(--ai-muted)}
.run-evidence p{margin:5px 0}.error{font-size:13px;color:#b42318;line-height:1.6}
.revision-form{display:grid;gap:14px;max-height:calc(88dvh - 140px);overflow:auto;padding-right:6px}.revision-options{display:grid;grid-template-columns:1fr 1fr;gap:12px}.revision-form :deep(textarea){font-size:15px;line-height:1.6}.revision-form .el-select{width:160px}
@media(max-width:900px){.ai-workbench{grid-template-columns:220px minmax(0,1fr)}.candidate-layout{grid-template-columns:160px minmax(0,1fr)}.candidate-detail{padding:14px}}
@media(max-width:650px){.ai-workbench{grid-template-columns:1fr;grid-template-rows:190px minmax(0,1fr);height:70vh}.generation-settings{border:0;border-bottom:1px solid var(--ai-line);padding-right:0}.candidate-layout{grid-template-columns:1fr}.candidate-list{max-height:110px;flex-direction:row}.candidate-list button{min-width:170px}.revision-options{grid-template-columns:1fr}}
</style>

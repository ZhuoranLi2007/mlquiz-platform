from pathlib import Path
p=Path('frontend/src/views/StudentFeedbackView.vue');s=p.read_text(encoding='utf-8-sig').replace("import ResponsivePaneTabs", "import RetestRoundList from '../components/RetestRoundList.vue'\nimport ResponsivePaneTabs",1)
a=s.index('    <el-drawer v-model="showRetests"');b=s.index('    <template #footer><StudentRetestControl',a)
s=s[:a]+'''    <el-drawer v-model="showRetests" title="复测记录" size="min(600px, 96vw)">
      <template v-if="detail"><p class="history-assessment">{{ detail.assessment.title }}</p><RetestRoundList :rounds="retests" @open="openRetest" /></template>
    </el-drawer>
'''+s[b:]
s+='\n<style scoped>.history-assessment{margin:0 0 18px;color:#6d839f;line-height:1.6;font-size:15px}.student-feedback-page :deep(.resource-notes){display:none}</style>\n';p.write_text(s,encoding='utf-8')
p=Path('frontend/src/components/TeacherRetestControl.vue');s=p.read_text(encoding='utf-8-sig').replace('size="min(840px, 96vw)"','size="min(680px, 96vw)"').replace('class="progress-row"','class="progress-row"');s+='\n<style scoped>.progress-row{padding:16px;border:1px solid #dce7f5;border-radius:12px;margin-top:14px;background:#fff}.progress-row header{align-items:start}.progress-row header>b{max-width:75%;font-size:15px;line-height:1.6}.progress-row p{line-height:1.6;margin:9px 0}.concept-tags{gap:6px}.progress-row .el-button{margin-top:4px}.progress-row header .el-tag{flex:none}.progress-row .concept-tags .el-tag{white-space:normal;height:auto;min-height:26px;line-height:1.5}</style>\n';p.write_text(s,encoding='utf-8')
p=Path('frontend/src/views/StudentPortalView.vue');s=p.read_text(encoding='utf-8-sig');s+='\n<style scoped>.student-task-panels{gap:12px}.task-grid{gap:12px}.recent-row{align-items:center}.records-layout{gap:12px}</style>\n';p.write_text(s,encoding='utf-8')

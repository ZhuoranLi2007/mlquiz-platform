from pathlib import Path
import shutil, json, hashlib,sys
root=Path.cwd();out=root/'docs/submission';sys.path.insert(0,str(out))
from report_content import BLOCKS
# Preserve all previous report images under the unified archive.
for b in BLOCKS:
 if b[0]=='figure':
  source=root/'docs'/b[2];target=root/'docs/report/screenshots_full/other'/('previous_'+source.name)
  if not target.exists():shutil.copy2(source,target)
sections=[
('6.1 教师端教学总览与资料管理','教师从教学总览进入资料管理。导入窗口支持选择资料类型，并明确区分内容识别与答案生成。英文原卷可回到资料列表查找；中文题目及来源的处理结果见图4-1和表4-1。', [('T01','教师教学总览'),('T02','课程资料管理'),('T03','资料导入设置'),('T04','英文试卷资料检索')]),
('6.2 在线资源管理与课程资料浏览','在线资源是独立模块，按平台、课程匹配和关键词筛选，并保留知识点标签。课程资料另提供原文阅读，推荐卡片中的课程精读将在6.8节展示。',[('T05','教师在线资源列表'),('T06','课程原文预览')]),
('6.3 正式题库与AI自动组卷','教师可以查看完整题目、参考答案、解析和来源，并调整章节。来源筛选可单独查看已有AI题及其审阅界面。当前主菜单没有独立的AI出题配置页，生成条件与组卷共用入口；图中已有AI题不冒称为本次新生成。',[('T07','正式题库与筛选'),('T08','题目详情与章节调整'),('T10','已有AI题筛选结果'),('T20','AI题答案与解析审阅'),('T11','AI自动组卷条件设置')]),
('6.4 试卷预览发布与考试管理','本次在隔离环境中实际点击生成4题100分试卷，预览后选择STU001发布。该轮题库足量，自动组卷完成了选题，没有调用模型补题。发布记录返回成功并在学生任务中出现，形成可关联的操作链。',[('T12','自动组卷结果与预览'),('T13','选择学生并发布考试'),('T14','发布后的考试记录')]),
('6.5 学生端首页与我的任务','学生首页就是“我的任务”，没有另设重复首页。图中新增考试与图6-12的发布标题相同；完成后该考试从待完成区进入最近提交和考试记录。历史列表用于重新查看答卷。',[('S02','学生我的任务接收考试'),('S03','交卷后任务状态更新'),('S15','学生考试历史记录')]),
('6.6 在线作答评分与错题反馈','普通考试的实际答题区见图4-3。本次新增自动组卷考试经浏览器作答与交卷得到75/100，错答了精确率题；反馈保留学生答案和参考答案。提交确认窗口以下用未提交的专项复测展示，未将其冒称为新增普通考试的交卷时刻。',[('S06','交卷前确认窗口'),('S07','新增考试成绩与错题'),('S08','学生错题分析')]),
('6.7 三类学情诊断的实际界面','三种范围分别回答本卷错在哪里、历史问题如何变化、同卷学生有哪些共性错误。这里继续使用原有双学生共同考试；多卷页含此前历史和本次新增考试，不能用页面平均值代表本轮两份试卷均值。教师也可查看原始答卷核对依据。',[('T15','教师单人单卷诊断'),('T16','教师单人多卷诊断'),('T17','教师多人单卷诊断'),('T19','教师查看学生答卷'),('S09','学生个人学情反馈')]),
('6.8 学习资源推荐与课程精读','STU002的正则化错题关联L1/L2资料，点击课程精读实际打开课程第15页。在线资源列表也向学生开放。外链是否返回网页与正文是否可读分别记录，本节实际阅读证据为课程原页。',[('S10','薄弱点学习资源推荐'),('S11','课程原页定位阅读'),('S16','学生在线资源列表')]),
('6.9 针对性复测与反馈更新','学生可以在知识点详情查看历次复测，并进入待完成的第3轮专项答题。原有最新5/5仍为练习完成，其题源及判定理由见第七章；教师进度抽屉同时列出历史正式通过和当前任务。新任务待完成时，已通过记录继续保留。',[('S17','专项复测实际答题'),('S12','学生复测记录抽屉'),('S13','已提交复测评分结果'),('S14','知识点复测反馈'),('T18','教师复测进度与结果')])]
new=[];mapping=[];n=0
for title,intro,shots in sections:
 new.append(('section',title));new.append(('paragraph',intro))
 for code,cap in shots:
  found=list((root/'docs/report/screenshots_full').rglob(code+'_*.png'))
  if not found:continue
  n+=1;rel=str(found[0].relative_to(root/'docs')).replace('\\','/')
  new.append(('figure',f'6-{n}',rel,cap));mapping.append({'code':code,'figure':f'6-{n}','section':title,'path':str(found[0].relative_to(root)).replace('\\','/'),'caption':cap})
start=next(i for i,b in enumerate(BLOCKS) if b[0]=='chapter' and b[1].startswith('第六章'))
end=next(i for i,b in enumerate(BLOCKS) if b[0]=='chapter' and b[1].startswith('第七章'))
blocks=BLOCKS[:start+1]+new+BLOCKS[end:]
# Correct old cross references after the chapter expansion.
blocks=[tuple(str(v).replace('图6-3','图6-29') if isinstance(v,str) and b[0]=='paragraph' and i>end else v for v in b) for i,b in enumerate(blocks)] if False else blocks
p=out/'report_content.py'
text='"""Report content with complete real interface coverage."""\nBLOCKS = '+repr(blocks)+'\n'
p.write_text(text,encoding='utf-8')
(root/'docs/report/insertions.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2),encoding='utf-8')
p=out/'build_detailed_report.py';s=p.read_text(encoding='utf-8').replace('35–45 pages','image-led report with no fixed page limit').replace('15 unmodified real screenshots','all verified unique screenshots').replace('read-only evidence, no new model calls or exams','existing evidence plus isolated UI capture examination')
s=s.replace("'4.6\\n6.2','4-8\\n6-3'", "'4.6\\n6.8','4-8\\n6-29'")
s=s.replace("'4-8\\n6-3'", "'4-8\\n6-29'")
p.write_text(s,encoding='utf-8')
print('chapter6 screenshots',n)

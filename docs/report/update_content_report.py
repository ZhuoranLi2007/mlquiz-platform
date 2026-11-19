from pathlib import Path
import sys,re,json,pprint
root=Path.cwd();out=root/'docs/submission';sys.path.insert(0,str(out));from report_content import BLOCKS
# One extra unique formula detail image; preserve all existing figures and shift later numbers once.
if not any(b[0]=='figure' and 'T23_' in str(b) for b in BLOCKS):
 def renumber(s):
  return re.sub(r'6-(\d+)',lambda m:'6-'+str(int(m[1])+1 if int(m[1])>=11 else int(m[1])),s)
 updated=[]
 for b in BLOCKS:
  b=tuple(renumber(v) if isinstance(v,str) else v for v in b)
  if b[0]=='figure' and b[1]=='6-10':
   b=(b[0],b[1],b[2],'偏差—方差规范定义题修订结果')
  updated.append(b)
  if b[0]=='figure' and b[1]=='6-10':updated.append(('figure','6-11','report/screenshots_full/teacher/T23_l2_formula_options.png','L2正则化公式选项修复结果'))
 BLOCKS=updated
 for i,b in enumerate(BLOCKS):
  if b[0]=='paragraph' and b[1].startswith('教师可以查看完整题目'):
   BLOCKS.insert(i+1,('paragraph','展示内容核对发现，原偏差—方差题将训练误差和训练／验证差距直接作为定义。修订题限定平方损失回归，在固定输入、同一学习方法和重复抽取训练集的条件下，分别解释平均预测的系统偏离与预测波动，答案保留B、2分。原课件第12页作为主题来源，定义按scikit-learn官方说明规范化。本题与相邻过拟合题调整至第6章模型评估；两道L2题按原页补齐四个公式选项，保留B，并移除自带答案的整页正文配图。图6-10与图6-11为修订后的真实界面。'))
   break
 for i,b in enumerate(BLOCKS):
  if b[0]=='paragraph' and b[1].startswith('上述结果引用11月19日保存'):
   BLOCKS[i]=('paragraph','表8-2引用11月19日原闭环验收日志。随后界面修复新增展示用例，前端共45项通过，类型检查与生产构建通过。1366×768浏览器核验题库详情、公式、试卷预览、学生作答前答案隔离及原成绩反馈，pageerror为0，刷新后修订仍存在。两个阶段的统计分别保留，未重新运行后端整套测试或新增模型调用。')
   BLOCKS.insert(i+1,('paragraph','本次仅在report-ui-20261119展示库通过现有修订接口创建4个题目新版本，脚本再次执行新增版本为0。先用SQLite备份，再对照修订前后记录；已有paper_items、attempts与answer_scores保持一致，原final-acceptance数据库哈希未变。另生成一份未发布的2题4分预览草稿验证新版本，未创建学生答卷，也未回写历史成绩。'))
   break
 BLOCKS.append(('paragraph','展示内容核对材料：docs/report/visible_content_revision.json记录版本与备份，visible_content_verification.json记录真实页面检查，revise_display_questions.py默认只读预览、显式--apply时修订展示库。规范定义参考scikit-learn官方Bias-variance decomposition示例：https://scikit-learn.org/stable/auto_examples/ensemble/plot_bias_variance.html。该材料属于交付前展示修订，不替代第七章原始实验记录。'))
 (out/'report_content.py').write_text('# Evidence-grounded report content; generated from reviewed source blocks.\nBLOCKS = '+pprint.pformat(BLOCKS,width=140,sort_dicts=False)+'\n',encoding='utf-8')
 mapping=json.loads((root/'docs/report/insertions.json').read_text(encoding='utf-8'))
 for m in mapping:
  m['figure']=renumber(m['figure'])
  if m['code']=='T20':m['caption']='偏差—方差规范定义题修订结果'
 j=next(i for i,m in enumerate(mapping) if m['code']=='T20')
 mapping.insert(j+1,{'code':'T23','figure':'6-11','section':'6.3 正式题库与AI自动组卷','path':'docs/report/screenshots_full/teacher/T23_l2_formula_options.png','caption':'L2正则化公式选项修复结果'})
 (root/'docs/report/insertions.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2),encoding='utf-8')
 p=out/'build_detailed_report.py';p.write_text(p.read_text(encoding='utf-8').replace('6-23','6-24'),encoding='utf-8')
p=root/'docs/report/build_inventory.py';s=p.read_text(encoding='utf-8')
s=s.replace('参考答案与评分规则正常，内容准确性另行核对','参考答案与评分规则正常；偏差—方差内容已按规范定义核对修订')
s=s.replace('浏览器核验见question_display_verification.json。偏差/方差题内容仍待人工复核。','偏差—方差定义、相邻章节与两道L2公式选项已核对并创建新版本；仅展示库修订。浏览器核验见visible_content_verification.json，版本依据见visible_content_revision.json。')
p.write_text(s,encoding='utf-8')
p=out/'报告界面截图补充说明.txt';s=p.read_text(encoding='utf-8')
s=s.replace('偏差/方差题的内容准确性仍需人工复核。','偏差／方差题已完成规范定义修订，见本文件最新补充；原观察保留在历史截图。')
p.write_text('最新补充：偏差—方差与公式题展示修订\n已核对原课件与scikit-learn官方定义，将偏差—方差题修订为平方损失回归定义题，答案B、2分；本题与相邻过拟合题归至第6章。两道L2题按原页恢复四个公式选项、答案B，移除正文中的含答案整页配图，原页引用保留。教师详情与预览统一为“参考答案”。\n仅修改data/report-ui-20261119展示库，通过既有接口建立4个新版本；重复执行不新增版本。原始验收库、已有试卷快照、答卷与历史分数不变。备份位于backups/20261119-184030-before-bias-content-fix，数据库备份与版本详情见../report/visible_content_revision.json。\n1366×768实拍T07/T08/T10/T20及两道L2题T23/T24，补充展开解析T20b、新版本预览T22与学生检查S18/S19。前端45项测试、类型检查、生产构建通过，浏览器检查无pageerror，刷新后持久化正常。未调用新模型、发布新考试或提交新答卷；新增一份未发布预览草稿。\n报告图6-10替换为修订后的偏差—方差题，新增图6-11公式题；后续图号顺延。旧问题截图在history目录保留，未修图或拼接。报告正文共42张不同原图，第六章30张；页面数以本次PDF与report_verification.json为准。\n\n以下为此前阶段说明，历史数量和操作按当时范围保留。\n\n'+s,encoding='utf-8')
p=out/'README.txt';s=p.read_text(encoding='utf-8');s=s.replace('当前交付：全界面图文版，58页，41张不同原图、6张技术图。','当前交付：内容核对后的全界面图文版，42张不同原图、6张技术图；页数见report_verification.json。');p.write_text('最新：偏差—方差规范定义与两道L2公式题已修订并实拍；仅展示库创建4个题目新版本，历史成绩不变。图6-10更新，新增图6-11，后续图号顺延。验证见../report/visible_content_verification.json。\n'+s,encoding='utf-8')
p=root/'docs/report/question_display_verification.json';r=json.loads(p.read_text(encoding='utf-8'));r['content_review_followup']={'status':'resolved_in_display_database','record':'visible_content_revision.json','verification':'visible_content_verification.json'};p.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
print('updated',len(BLOCKS),'blocks')

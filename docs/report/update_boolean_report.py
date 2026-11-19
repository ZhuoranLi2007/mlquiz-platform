from pathlib import Path
import sys,pprint
p=Path('docs/submission/report_content.py');sys.path.insert(0,str(p.parent));from report_content import BLOCKS
for i,b in enumerate(BLOCKS):
 if b[0]=='paragraph' and b[1].startswith('表8-2引用') and '折行' not in b[1]:BLOCKS[i]=(b[0],b[1]+'另修复教师答卷与学生成绩中判断选项因固定窄列产生的折行，浏览器检查“正确／错误”均保持单行。')
p.write_text('# Evidence-grounded report content; generated from reviewed source blocks.\nBLOCKS = '+pprint.pformat(BLOCKS,width=140,sort_dicts=False)+'\n',encoding='utf-8')
p=Path('docs/submission/报告界面截图补充说明.txt');s=p.read_text(encoding='utf-8');s=s.replace('教师详情与预览统一为“参考答案”。','教师详情与预览统一为“参考答案”。另修复教师答卷与学生成绩判断选项被窄列挤成两行的问题，重拍T19/S19，并检查标签实际高度为单行。');p.write_text(s,encoding='utf-8')
p=Path('docs/submission/verify_report.py');s=p.read_text(encoding='utf-8').replace("rendered_pages('final-release'", "rendered_pages('delivery'").replace("ROOT/'frontend/src/views/PaperBuilderView.vue']", "ROOT/'frontend/src/views/PaperBuilderView.vue', ROOT/'frontend/src/views/TeacherAttemptReviewView.vue', ROOT/'frontend/src/components/StudentReviewQuestion.vue']");p.write_text(s,encoding='utf-8')
p=Path('docs/report/fix_boolean_layout.py');s=p.read_text(encoding='utf-8').replace(" s=s.replace('</style>'", " if '.option.boolean-option{' not in s:s=s.replace('</style>'");p.write_text(s,encoding='utf-8')

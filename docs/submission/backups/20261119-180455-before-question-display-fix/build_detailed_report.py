"""Rebuild the report from evidence; preserve the backed-up Word style system.

Run with the bundled document runtime. PDF bookmarks supply cached TOC pages:
  python build_detailed_report.py --toc-pdf path/to/render.pdf
Render with the documents skill after every change. Does not touch application data.
"""
from pathlib import Path
import argparse, hashlib, json, re, math
from collections import Counter
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont
from report_content import BLOCKS

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
QA = ROOT / '.artifact-output/report-rewrite'
QA.mkdir(parents=True, exist_ok=True)
BACKUP = OUT / 'backups/20261119-132433-before-rewrite'
REFERENCE = BACKUP / 'course_report_detailed.docx'
FONT = 'C:/Windows/Fonts/msyh.ttc'
FONTB = 'C:/Windows/Fonts/msyhbd.ttc'
parser = argparse.ArgumentParser()
parser.add_argument('--toc-pdf')
args = parser.parse_args()

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
for item in json.loads((BACKUP/'sha256.json').read_text(encoding='utf-8-sig')):
    assert sha(BACKUP/item['name']).upper() == item['sha256'], 'Backup changed'

(QA/'artifact.md').write_text('''# Report rewrite contract
Reference: docs/submission/backups/20261119-132433-before-rewrite/course_report_detailed.docx
Reference SHA256: E51FAEEE80F2F7E8BF4E86E1185F78A7EF5280F1599DA4F9AC9C2728295A9F27
User authorizes complete body rewrite to nine chapters, image-led report with no fixed page limit, not application changes.
Preserve Letter portrait; margins .72/.70/.85/.85 inches; original Word styles and header/footer.
Normal 11pt Song + Times New Roman, 1.3 line spacing; heading black Microsoft YaHei.
Add editable TOC field, all verified unique screenshots, six source-aligned diagrams and evidence tables.
Keep paragraphs flowing; chapter starts may break. Repeat table headers. No orphan captions.
Use source line extracts for code; existing evidence plus isolated UI capture examination.
Deliver Word + matching PDF, landscape six-column traceability matrix, rewrite and personal-material notes.
Render all documents and inspect all page PNGs before delivery. Existing PPT stays untouched.
''', encoding='utf-8')

def canvas(w=1800,h=1100):
    im=Image.new('RGB',(w,h),'white'); return im,ImageDraw.Draw(im)
def text_center(d,xy,text,size=38,bold=False,color='#182735'):
    font=ImageFont.truetype(FONTB if bold else FONT,size)
    lines=text.split('\n'); lh=size*1.45
    for i,line in enumerate(lines):
        d.text((xy[0],xy[1]+(i-(len(lines)-1)/2)*lh),line,font=font,fill=color,anchor='mm')
def box(d,rect,title,sub='',size=37):
    d.rounded_rectangle(rect,14,fill='#F0F6FB',outline='#708AA0',width=3)
    x=(rect[0]+rect[2])/2; y=(rect[1]+rect[3])/2
    text_center(d,(x,y-25 if sub else y),title,size,True)
    if sub: text_center(d,(x,y+35),sub,30)
def arrow(d,a,b,label='',dashed=False):
    if dashed:
        length=math.dist(a,b)
        for start in range(0,int(length),24):
            end=min(start+13,length)
            d.line([(a[0]+(b[0]-a[0])*start/length,a[1]+(b[1]-a[1])*start/length),
                    (a[0]+(b[0]-a[0])*end/length,a[1]+(b[1]-a[1])*end/length)],fill='#45657E',width=3)
    else: d.line([a,b],fill='#45657E',width=4)
    angle=math.atan2(b[1]-a[1],b[0]-a[0]); r=16
    d.polygon([b,(b[0]-r*math.cos(angle-.5),b[1]-r*math.sin(angle-.5)),(b[0]-r*math.cos(angle+.5),b[1]-r*math.sin(angle+.5))],fill='#45657E')
    if label:
        x=(a[0]+b[0])/2; y=(a[1]+b[1])/2
        f=ImageFont.truetype(FONT,29); bb=d.textbbox((x,y),label,font=f,anchor='mm')
        d.rectangle((bb[0]-9,bb[1]-5,bb[2]+9,bb[3]+5),fill='white')
        d.text((x,y),label,font=f,fill='#182735',anchor='mm')
def save(im,key):
    path=QA/(key+'.png'); im.save(path); return path
def graph(key,nodes,footer):
    h=len(nodes)*145+150
    im,d=canvas(1800,h)
    for i,(name,desc) in enumerate(nodes):
        y=40+i*145
        box(d,(170,y,1630,y+110),name,desc,35)
        if i<len(nodes)-1: arrow(d,(900,y+110),(900,y+145))
    if key=='diagnosis_graph':
        d.line([(170,95),(65,95),(65,965)],fill='#45657E',width=4)
        arrow(d,(65,965),(170,965))
        text_center(d,(82,520),'无\n效\n答\n卷\n中\n止',29)
    text_center(d,(900,h-45),footer,29)
    return save(im,key)

diagrams={}
im,d=canvas(1800,1050)
box(d,(180,25,1620,160),'Vue 3 教师与学生工作台','TypeScript · Element Plus · ECharts')
box(d,(180,250,1620,390),'FastAPI 接口与业务服务','身份和范围校验 · 评分 · 诊断 · 资源 · 学习证据')
box(d,(100,500,820,650),'后台任务与 LangGraph','worker · 节点状态 · Skill 调用')
box(d,(990,500,1710,650),'模型适配服务','生成候选 · 中文解释 · 要点评分')
box(d,(180,800,1620,960),'SQLite 与课程文件目录','版本 · 快照 · 答卷 · 任务 · SkillRun · 原页图片')
arrow(d,(900,160),(900,250),'API 请求 / 状态查询')
arrow(d,(460,390),(460,500)); arrow(d,(820,575),(990,575),'按需调用')
arrow(d,(460,650),(460,800),'结果持久化'); arrow(d,(1350,390),(1350,500))
diagrams['architecture']=save(im,'architecture')
im,d=canvas(1800,810)
rows=[['资料导入','题目提取与审核','正式题库'],['教师组卷','发布考试','学生作答与评分'],['学情诊断','薄弱点学习资源','复测与证据更新']]
for ri,row in enumerate(rows):
    for ci,label in enumerate(row):
        x=60+ci*590; y=35+ri*245
        box(d,(x,y,x+490,y+130),label,size=37)
        if ci<2: arrow(d,(x+490,y+65),(x+590,y+65))
    if ri<2:
        y=165+ri*245
        d.line([(1730,y),(1765,y),(1765,y+65),(35,y+65),(35,y+180)],fill='#45657E',width=4)
        arrow(d,(35,y+180),(60,y+180))
text_center(d,(900,765),'复测结果回到诊断与推荐，形成下一轮学习任务',33)
diagrams['business']=save(im,'business')
im,d=canvas(1800,1370)
# An intentionally selective ER view; all arrows represent logical associations.
entries=[((60,50,540,210),'Document / Chunk','资料及版本与文本片段'),((660,50,1140,210),'KnowledgePoint','知识点与章节名称'),((1260,50,1740,210),'Resource','课程页 / 外链 + kp_ids'),
((60,430,540,590),'Question','同一题目的不同版本行'),((660,430,1140,590),'PaperItem','题项快照 / 分值 / 来源'),((1260,430,1740,590),'AssessmentVersion','固定试卷版本 / 发布'),
((60,810,540,970),'TargetedRetestCycle','原答卷 / 目标 / 题目证据'),((660,810,1140,970),'Answer / AnswerScore','逐题答案 / 分数 / 依据'),((1260,810,1740,970),'Attempt','学生 / 版本 / 提交状态'),
((660,1100,1140,1220),'Diagnosis','范围 / 结果 / 证据引用')]
for rect,title,sub in entries: box(d,rect,title,sub,34)
arrow(d,(300,210),(300,430),'来源引用 1:N',True)
arrow(d,(900,210),(900,330),'知识点标签',True)
d.line([(900,330),(460,330)],fill='#45657E',width=3);arrow(d,(460,330),(460,430),dashed=True)
arrow(d,(1140,130),(1260,130),'N:M',True)
arrow(d,(540,510),(660,510),'引用版本',True)
arrow(d,(1260,510),(1140,510),'1:N',True)
arrow(d,(1500,590),(1500,810),'1:N',True)
arrow(d,(900,590),(900,810),'题项关联',True)
arrow(d,(1260,890),(1140,890),'1:N',True)
arrow(d,(540,890),(660,890),'证据引用',True)
d.line([(1500,970),(1500,1070),(300,1070)],fill='#45657E',width=3);arrow(d,(300,1070),(300,970),dashed=True)
text_center(d,(900,1035),'原答卷与复测答卷关联轮次',29)
d.line([(1140,940),(1195,940),(1195,1160)],fill='#45657E',width=3);arrow(d,(1195,1160),(1140,1160),dashed=True)
text_center(d,(1445,1160),'读取评分并按范围汇总',29)
text_center(d,(900,1280),'连线表示业务关联；虚线为引用，未宣称全部为 SQL 外键',30)
text_center(d,(900,1330),'同框实体为阅读分组；具体键与快照字段见表 3-1',30)
diagrams['er']=save(im,'er')
diagrams['diagnosis_graph']=graph('diagnosis_graph',[
('score_check','检查提交状态与已有评分；有效时进入下一节点'),('question_diagnoses','逐题调用 diagnose_question'),('paper_diagnosis','调用 diagnose_paper'),('history_diagnosis','调用 diagnose_history'),('recommend','调用 recommend_resources'),('update_plan','生成或更新学习计划'),('finalize','汇总 outputs 与 errors')], 'START → score_check；finalize → END；包含中止分支')
diagrams['retest_graph']=graph('retest_graph',[
('diagnose','解析授权范围与目标；按条件调用 diagnose_classroom'),('recommend_resources','为目标与剩余薄弱点读取学习资源'),('generate_retest_candidates','按条件调用生成图；收集合格候选'),('validate_and_publish_retest','校验候选 / 题库补足 / 练习降级 / 续做')], 'START → 四个实际节点 → END；分支逻辑在节点内部')
diagrams['post_graph']=graph('post_graph',[
('diagnose_submitted_attempt','单卷诊断并取得最新薄弱点'),('update_longitudinal_diagnosis','更新该学生的多卷诊断'),('refresh_knowledge_state','根据有效作答刷新知识状态'),('compare_retest','有原答卷时比较并调用 verify_learning'),('refresh_recommendations','为当前薄弱点刷新资源')], 'START → 五个实际节点 → END；来源 graphs/classroom_workflow.py')

def base_doc():
    doc=Document(REFERENCE)
    body=doc._element.body
    for child in list(body):
        if child.tag!=qn('w:sectPr'): body.remove(child)
    for st in doc.styles:
        for border in list(st.element.iter(qn('w:pBdr'))): border.getparent().remove(border)
    doc.core_properties.title='机器学习小测平台课程实验报告'
    doc.core_properties.subject='设计实现与学习闭环验收；源码基线 21c58b0'
    doc.core_properties.author=''
    doc.styles['Normal'].paragraph_format.space_after=Pt(5)
    st=doc.styles.add_style('Frontmatter',WD_STYLE_TYPE.PARAGRAPH)
    st.base_style=doc.styles['Heading 1']
    lvl=OxmlElement('w:outlineLvl');lvl.set(qn('w:val'),'9');st.element.get_or_add_pPr().append(lvl)
    for level in [1,2]:
        name=f'TOC {level}'
        st=doc.styles[name] if name in doc.styles else doc.styles.add_style(name,WD_STYLE_TYPE.PARAGRAPH)
        st.base_style=doc.styles['Normal'];st.font.size=Pt(10.5);st.font.bold=level==1
        pf=st.paragraph_format;pf.first_line_indent=Pt(0);pf.left_indent=Pt(0 if level==1 else 16);pf.space_after=Pt(3);pf.line_spacing=1.12
        pf.tab_stops.add_tab_stop(Inches(6.55),WD_TAB_ALIGNMENT.RIGHT,WD_TAB_LEADER.DOTS)
    update=doc.settings.element.find(qn('w:updateFields'))
    if update is None: update=OxmlElement('w:updateFields');doc.settings.element.append(update)
    update.set(qn('w:val'),'true')
    return doc
def para(doc,text,style=None,indent=True):
    p=doc.add_paragraph(text,style)
    if not indent: p.paragraph_format.first_line_indent=Pt(0)
    p.paragraph_format.widow_control=True
    return p
def caption(doc,text,before=False):
    p=para(doc,text,'Caption',False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next=before
    return p
def table(doc,headers,rows,widths,size=9.5):
    t=doc.add_table(rows=1,cols=len(headers));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
    for i,w in enumerate(widths):t.columns[i].width=Inches(w)
    for ri,values in enumerate([headers,*rows]):
        cells=t.rows[0].cells if ri==0 else t.add_row().cells
        rp=cells[0]._tc.getparent().get_or_add_trPr();rp.append(OxmlElement('w:cantSplit'))
        if ri==0: rp.append(OxmlElement('w:tblHeader'))
        for ci,text in enumerate(values):
            c=cells[ci];c.width=Inches(widths[ci]);c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            pr=c._tc.get_or_add_tcPr();mar=OxmlElement('w:tcMar')
            for side in ['top','bottom','left','right']:
                e=OxmlElement('w:'+side);e.set(qn('w:w'),'65');e.set(qn('w:type'),'dxa');mar.append(e)
            pr.append(mar)
            borders=OxmlElement('w:tcBorders')
            for side in ['top','bottom','left','right']:
                e=OxmlElement('w:'+side);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'D9D9D9');borders.append(e)
            pr.append(borders)
            if ri==0:
                shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'DDEBF7');pr.append(shade)
            p=c.paragraphs[0];p.paragraph_format.first_line_indent=Pt(0);p.paragraph_format.space_after=Pt(2);p.paragraph_format.space_before=Pt(2);p.paragraph_format.line_spacing=1.15
            p.alignment=WD_ALIGN_PARAGRAPH.CENTER if widths[ci]<1.05 or ri==0 else WD_ALIGN_PARAGRAPH.LEFT
            r=p.add_run(str(text));r.font.size=Pt(size);r.bold=ri==0
    p=para(doc,'',indent=False);p.paragraph_format.space_after=Pt(2);p.paragraph_format.line_spacing=Pt(2);p.paragraph_format.space_before=Pt(0);p.paragraph_format.keep_with_next=True
    return t
def fieldrun(p,kind,text=None):
    r=OxmlElement('w:r')
    e=OxmlElement('w:instrText' if kind=='instr' else 'w:fldChar')
    if kind=='instr':e.set(qn('xml:space'),'preserve');e.text=text
    else:e.set(qn('w:fldCharType'),kind)
    r.append(e);p._p.append(r)
def add_picture(doc,path,title,width=6.6):
    p=para(doc,'',indent=False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next=True;p.paragraph_format.space_before=Pt(4);p.paragraph_format.space_after=Pt(4)
    p.add_run().add_picture(str(path),width=Inches(width))
    caption(doc,title)

pages={}
if args.toc_pdf:
    from pypdf import PdfReader
    reader=PdfReader(args.toc_pdf)
    def visit(items):
        for x in items:
            if isinstance(x,list): visit(x)
            else: pages[x.title]=reader.get_destination_page_number(x)+1
    visit(reader.outline)

doc=base_doc()
p=para(doc,'机器学习小测平台','Title',False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.space_before=Pt(95)
p=para(doc,'课程实验报告','Subtitle',False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.space_after=Pt(45)
p=para(doc,'系统设计  关键实现  学习闭环验收',indent=False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER
for t in ['姓名：________________    学号：________________','班级：________________    指导教师：____________','小组成员与分工：________________________________']:
    p=para(doc,t,indent=False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.space_before=Pt(17)
p=para(doc,'资料与验证记录截至 2026年11月19日',indent=False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.space_before=Pt(45)
p=para(doc,'源码基线 21c58b0',indent=False);p.alignment=WD_ALIGN_PARAGRAPH.CENTER
p=para(doc,'摘 要','Frontmatter',False);p.paragraph_format.page_break_before=True
para(doc,'本实验围绕机器学习课程中的课堂小测与课后复习，设计并实现一个连接资料、题库、考试、学情诊断、学习资源与复测的平台。系统以教师和学生为主要使用者，采用Vue 3与FastAPI构建交互和业务接口，以SQLite保存试卷版本、答卷、评分及学习证据，并通过LangGraph编排业务Skill和模型调用。报告按需求、架构、数据模型、核心实现和真实案例解释各模块的输入、处理及输出。')
para(doc,'系统将确定性规则与AI能力分开：客观题由程序评分，主观题按评分要点处理，章节统计只使用有效评分；模型负责候选生成和解释，程序检查课程范围、答案可靠性和题目独立性。复测分别记录整轮通过、知识点达标与练习完成，避免把重复题全对直接当成新题验证。教师与学生共享同一组学习证据，并通过任务记录与反馈页面连接下一轮学习。')
para(doc,'现有验收材料显示，两名学生在同一试卷均得75/100，但错误分别位于模型评估和正则化，章节诊断与推荐因此不同。学生A随后完成5题重复练习并得10/10，再完成第二份试卷得50/50；这些记录在刷新和重启后可读回。报告同时分析英文资料导入、此前正式AI复测与章节映射修复，逐项区分案例日期和数据范围。后端215项与前端38项自动化测试为规则和交互提供补充证据。')
para(doc,'实验说明：学习闭环的关键是让来源、题目、答卷、薄弱点和后续证据能够关联，而不是仅生成一段建议。当前成果支持课程实验和单机演示；模型候选独立性、长期学习效果及部分外部资源访问仍有进一步完善空间。',indent=True)
para(doc,'关键词：机器学习课程；学情诊断；LangGraph；学习证据；复测；软件测试',indent=False)
p=para(doc,'目 录','Frontmatter',False);p.paragraph_format.page_break_before=True
toc_entries=[(b[1],1 if b[0]=='chapter' else 2) for b in BLOCKS if b[0] in ('chapter','section')]
for i,(title,level) in enumerate(toc_entries):
    p=para(doc,'',f'TOC {level}',indent=False);p.paragraph_format.space_after=Pt(3);p.paragraph_format.line_spacing=1.12
    p.paragraph_format.left_indent=Pt(0 if level==1 else 16)
    p.paragraph_format.tab_stops.add_tab_stop(Inches(6.55),WD_TAB_ALIGNMENT.RIGHT,WD_TAB_LEADER.DOTS)
    if i==0:
        fieldrun(p,'begin');fieldrun(p,'instr',' TOC \\o "1-2" \\h \\z \\u ');fieldrun(p,'separate')
    r=p.add_run(title+'\t'+str(pages.get(title,' ')));r.font.size=Pt(10.5);r.bold=level==1
    if i==len(toc_entries)-1:fieldrun(p,'end')

manifest=[]
for b in BLOCKS:
    kind=b[0]
    if kind=='chapter':
        p=doc.add_heading(b[1],1);p.paragraph_format.page_break_before=not b[1].startswith('附录')
        if b[1].startswith('附录'):p.paragraph_format.space_before=Pt(16)
    elif kind=='section':doc.add_heading(b[1],2)
    elif kind=='paragraph':para(doc,b[1])
    elif kind in ('figure','diagram'):
        path=ROOT/'docs'/b[2] if kind=='figure' else diagrams[b[2]]
        assert path.is_file(),path
        width=6.6
        add_picture(doc,path,'图'+b[1]+'  '+b[3],width)
        manifest.append({'number':b[1],'type':kind,'source':str(path.relative_to(ROOT)),'sha256':sha(path)})
    elif kind=='table':
        caption(doc,'表'+b[1]+'  '+b[2],True);table(doc,b[3],b[4],b[5])
    elif kind=='code':
        lines=(ROOT/b[3]).read_text(encoding='utf-8').splitlines()[b[4]-1:b[5]]
        caption(doc,'代码'+b[1]+'  '+b[2],True)
        for line in lines:
            p=para(doc,line,indent=False);p.paragraph_format.space_after=Pt(0);p.paragraph_format.line_spacing=1.05;p.paragraph_format.keep_with_next=True
            for r in p.runs:r.font.name='Consolas';r.font.size=Pt(9)
        p=para(doc,f'来源：{b[3]}，第{b[4]}—{b[5]}行。',indent=False)
        for r in p.runs:r.font.size=Pt(8.5)
    elif kind=='literal':
        p=para(doc,b[1],indent=False);p.paragraph_format.line_spacing=1.15
        for r in p.runs:r.font.name='Consolas';r.font.size=Pt(9)
doc.save(OUT/'course_report_detailed.docx')
(QA/'figure_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')

matrix_rows=[
['基础题库与AI生成题','正式题库、候选生成、来源审核','unified_import.py；workflows.py::build_generation_graph','题库截图；英文导入8题；最新生成5候选全因重复被拒','4.1—4.2\n5.3','4-1\n4-2'],
['题目层学情诊断','题项评分、错误依据与班级题目分析','classroom_diagnosis.py::question_analysis；scoring.py','共同考试4题；前两题各50%正确；逐题评分读回','4.4—4.5\n7.1','4-4\n4-7'],
['试卷层学情诊断','单人单卷章节和知识点汇总','classroom_diagnosis.py::student_attempt','A与B均75分，薄弱章节不同；readback.json','4.5\n7.1','4-5'],
['同一学生多次考试','个人历史、变化和反复错误','classroom_diagnosis.py::student_history','A第二卷50/50；历史含此前记录，页面71%非两卷均值','4.5\n7.2','4-6'],
['同卷多名学生','班级分布、共同与个体问题','classroom_diagnosis.py::class_assessment','共同考试2人均75/100；班级接口和页面核验','4.5\n7.1','4-7'],
['根据诊断推荐学习内容','知识点匹配课程原页与相关外链','learning_resources.py；recommend_resources Skill','B打开L1/L2课程原页；受限链接不算内容已验证','4.6\n6.8','4-8\n6-23'],
['设计新题检验学习','自主复测、备用题源、证据判定','learning_loop.py::prepare_questions；learning_evidence.py','最新练习5/5；此前正式复测A全对、B4/5；案例分开','4.7\n7.3','4-9\n7-1\n7-2—3'],
['学习内容库 B站 帖子 抖音','多种资源记录与访问质量筛选','learning_resources.py；资源库数据','35知识点82课程页引用；抖音受限后移出默认推荐，非全部外链通过','4.6\n8.4','4-8\n6-23'],
['LangGraph或Pi','实际StateGraph与后台任务','graphs/workflows.py、learning_loop.py、classroom_workflow.py','节点定义、任务stages与SkillRun可对应；本项目采用LangGraph','2.1\n第五章','2-1\n5-1—3'],
['三层诊断 推荐 出题 检测做成Skill','六类核心能力与课堂范围封装','skills/registry.py；services/skill_runtime.py','注册表与运行记录；不声称每次调用全部Skill','5.1—5.5','5-1—3'],
['课程实践实验报告','本次完整重写报告','report_content.py；build_detailed_report.py','可编辑Word与对应PDF；源码、截图和案例交叉核对','全篇','全篇'],
['现场展示PPT','已有course_demo.pptx','docs/submission/course_demo.pptx','已有文件，本次未修改；须本人对照新报告更新并演练','附录C','—'],
['使用的提示词历史','开发指令、运行日志、模板分别保存','docs/PROMPT_HISTORY.md及各专项runtime-prompts.json','可核验运行日志；指定开发平台完整早期记录待补','5.5\n附录C','—'],
['豆包Work或WorkBuddy开发平台','待补指定平台使用原始凭证','不以应用的LangGraph或模型日志替代','尚缺可靠完整凭证，不能判为已满足','1.2\n附录C','—'],
['最多2人1组 截止2026年12月2日','个人信息、分工与提交安排','封面；待本人补充事项.txt','人数与分工待本人填写；日期按老师原截图','封面\n1.2','—'],
['匿名小测表与题目附件','作为课程背景与历史数据来源核对','资料来源与导入记录；不外推未核验全量附件','报告仅据已有记录分析；老师原始两附件版本和完整导入覆盖待补核对','1.2\n附录A','—'],
]
matrix=base_doc();sec=matrix.sections[0];sec.orientation=WD_ORIENT.LANDSCAPE
matrix.core_properties.title='课程要求与项目实现对照表'
sec.header.paragraphs[0].text='机器学习小测平台  课程要求与项目实现对照'
sec.header.paragraphs[0].runs[0].font.size=Pt(8)
sec.page_width=Inches(11);sec.page_height=Inches(8.5);sec.left_margin=sec.right_margin=Inches(.55);sec.top_margin=sec.bottom_margin=Inches(.55)
sec.different_first_page_header_footer=False
p=para(matrix,'课程要求与项目实现对照表','Title',False);p.runs[0].font.size=Pt(19)
para(matrix,'依据老师原始任务截图核对。实现与证据截至2026年11月19日；源码基线21c58b0。具体阈值、固定工作台和备用题源属于项目设计。',indent=False)
table(matrix,['老师要求','对应功能','源码文件或函数','验证证据','报告章节','截图编号'],matrix_rows,[1.35,1.5,2.35,3.05,.8,.75],9.5)
para(matrix,'路径说明：未列前缀的服务文件位于backend/app/services；graphs、skills位于backend/app。图号对应重写报告；5-1至5-3为工作流图。原始要求中未单列源代码交付，源码作为本项目配套材料保留。',indent=False)
matrix.save(OUT/'课程要求与项目实现对照表_全界面版.docx')
stats={'chapters':9,'body_chinese_characters':len(re.findall('[\u4e00-\u9fff]',str(BLOCKS))),
       'screenshots':sum(b[0]=='figure' for b in BLOCKS),'diagrams':sum(b[0]=='diagram' for b in BLOCKS),
       'tables':sum(b[0]=='table' for b in BLOCKS),'code_excerpts':sum(b[0]=='code' for b in BLOCKS),
       'toc_entries':len(toc_entries),'toc_pages_resolved':len(pages),'word_sha256':sha(OUT/'course_report_detailed.docx')}
(QA/'build_stats.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(stats,ensure_ascii=False))

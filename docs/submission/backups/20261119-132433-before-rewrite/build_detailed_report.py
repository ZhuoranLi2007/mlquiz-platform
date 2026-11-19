"""Build the detailed course report from verified repository evidence.

Run with the bundled document runtime. Render with the documents skill before delivery.
"""
from pathlib import Path
import re
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SHOTS = ROOT / 'docs/acceptance/screenshots'
QA = ROOT / '.artifact-output/report-qa'
QA.mkdir(parents=True, exist_ok=True)
doc = Document()
for style in doc.styles:
    for border in list(style.element.iter(qn('w:pBdr'))):
        border.getparent().remove(border)
sec = doc.sections[0]
sec.page_width, sec.page_height = Inches(8.5), Inches(11)
sec.top_margin, sec.bottom_margin = Inches(.72), Inches(.7)
sec.left_margin = sec.right_margin = Inches(.85)
sec.header_distance = sec.footer_distance = Inches(.3)
for name in ['Normal', 'Title', 'Subtitle', 'Heading 1', 'Heading 2', 'Heading 3', 'Caption']:
    st = doc.styles[name]
    st.font.name = 'Times New Roman'
    st.font.color.rgb = RGBColor(0, 0, 0)
    st.font.italic = False
    st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '宋体' if name == 'Normal' else '微软雅黑')
    st.paragraph_format.space_after = Pt(7)
normal = doc.styles['Normal']
normal.font.size = Pt(11)
normal.paragraph_format.line_spacing = 1.3
normal.paragraph_format.first_line_indent = Pt(22)
for name, size in [('Title', 25), ('Subtitle', 14), ('Heading 1', 18), ('Heading 2', 12.5), ('Heading 3', 11.5)]:
    doc.styles[name].font.size = Pt(size)
    doc.styles[name].font.bold = name != 'Subtitle'
    doc.styles[name].paragraph_format.first_line_indent = Pt(0)
    doc.styles[name].paragraph_format.space_before = Pt(10 if name == 'Heading 2' else 0)
doc.styles['Caption'].font.size = Pt(9)
doc.styles['Caption'].font.italic = False
doc.styles['Caption'].paragraph_format.first_line_indent = Pt(0)
doc.styles['Caption'].paragraph_format.space_after = Pt(9)
sec.different_first_page_header_footer = True
hp = sec.header.paragraphs[0]
hp.text = '机器学习小测平台  软件工程课程实验报告'
hp.paragraph_format.first_line_indent = Pt(0)
for r in hp.runs:
    r.font.size = Pt(8)
    r.font.color.rgb = RGBColor(0, 0, 0)
fp = sec.footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
fp.paragraph_format.first_line_indent = Pt(0)
fp.add_run('第 ')
field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE'); fp._p.append(field)
fp.add_run(' 页')
doc.core_properties.title = '机器学习小测平台设计实现与学习闭环验收实验报告'
doc.core_properties.subject = '课程实践报告；源码基线 21c58b0；真实验收记录截至 2026年11月19日'
doc.core_properties.author = ''
doc.core_properties.keywords = '机器学习, 学情诊断, LangGraph, 复测, 软件测试'

def p(text, style=None, indent=True):
    para = doc.add_paragraph(text, style)
    if not indent: para.paragraph_format.first_line_indent = Pt(0)
    para.paragraph_format.widow_control = True
    return para

def h(text):
    doc.add_heading(text, 2)

def page(title):
    heading = doc.add_heading(title, 1)
    heading.paragraph_format.page_break_before = True

def table(headers, rows, widths):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, w in enumerate(widths): t.columns[i].width = Inches(w)
    for row in [t.rows[0], *[t.add_row() for _ in rows]]:
        for i, cell in enumerate(row.cells):
            cell.width = Inches(widths[i]); cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            pr = cell._tc.get_or_add_tcPr()
            margins = OxmlElement('w:tcMar')
            for side in ['top', 'left', 'bottom', 'right']:
                x = OxmlElement('w:'+side); x.set(qn('w:w'), '85'); x.set(qn('w:type'), 'dxa'); margins.append(x)
            pr.append(margins)
        trpr = row._tr.get_or_add_trPr()
        trpr.append(OxmlElement('w:cantSplit'))
    for ri, values in enumerate([headers, *rows]):
        for ci, val in enumerate(values):
            cell = t.cell(ri, ci); cell.text = str(val)
            for para in cell.paragraphs:
                para.paragraph_format.first_line_indent = Pt(0)
                para.paragraph_format.space_after = Pt(2)
                para.paragraph_format.space_before = Pt(2)
                para.paragraph_format.line_spacing = 1.15
                if (ci == 0 and len(str(val)) < 15) or re.fullmatch(r'[0-9.]+', str(val)):
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in para.runs:
                    run.font.size = Pt(9.5); run.bold = ri == 0
            if ri == 0:
                sh = OxmlElement('w:shd'); sh.set(qn('w:fill'), 'E8EFF5'); cell._tc.get_or_add_tcPr().append(sh)
    repeat = OxmlElement('w:tblHeader'); t.rows[0]._tr.get_or_add_trPr().append(repeat)
    borders = OxmlElement('w:tblBorders')
    for side in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        b = OxmlElement('w:'+side); b.set(qn('w:val'), 'single'); b.set(qn('w:sz'), '4'); b.set(qn('w:color'), 'D9D9D9'); borders.append(b)
    t._tbl.tblPr.append(borders)
    p('', indent=False).paragraph_format.space_after = Pt(1)
    return t

figure_count = 0
def fig(filename, caption, width=6.65):
    global figure_count
    figure_count += 1
    para = p('', indent=False)
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.keep_with_next = True
    para.paragraph_format.space_after = Pt(3)
    para.add_run().add_picture(str(SHOTS / filename), width=Inches(width))
    cap = p(f'图{figure_count}  {caption}', 'Caption', False)
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER

def architecture():
    im = Image.new('RGB', (1600, 700), 'white'); d = ImageDraw.Draw(im)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 29)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 24)
    def box(x,y,w,hh,title,detail):
        d.rounded_rectangle((x,y,x+w,y+hh), radius=10, fill='#F4F8FC', outline='#7792AD', width=2)
        d.text((x+w/2,y+24),title,font=font,fill='black',anchor='mt')
        d.text((x+w/2,y+72),detail,font=small,fill='#333333',anchor='mt')
    def arrow(a,b):
        d.line([a,b],fill='#51677D',width=4)
        x,y=b; d.polygon([(x,y),(x-9,y-15),(x+9,y-15)],fill='#51677D')
    box(50,15,700,125,'教师端','资料导入  题库组卷  发布考试  三类诊断')
    box(850,15,700,125,'学生端','考试作答  查看反馈  打开资料  自主复测')
    arrow((400,140),(400,205)); arrow((1200,140),(1200,205))
    box(50,205,1500,125,'FastAPI 与共享业务服务','身份校验  版本快照  评分统计  学习证据判定  章节关联')
    arrow((400,330),(400,395)); arrow((1200,330),(1200,395))
    box(50,395,700,125,'LangGraph 与后台任务','诊断  推荐  候选生成  验答发布  重试恢复')
    box(850,395,700,125,'SQLite 与本地课程资源','题目版本  答卷评分  复测记录  提示词与审计')
    d.line([(750,455),(850,455)], fill='#51677D',width=4)
    d.text((800,590),'模型提供候选与解释  程序依据真实记录判定结果',font=font,fill='black',anchor='mt')
    path=QA/'architecture.png'; im.save(path)
    para=p('',indent=False); para.alignment=WD_ALIGN_PARAGRAPH.CENTER
    para.add_run().add_picture(str(path),width=Inches(6.65))
    cap=p('系统架构示意图  根据当前源码绘制', 'Caption',False); cap.alignment=WD_ALIGN_PARAGRAPH.CENTER

# 1 Cover
p('').paragraph_format.space_after = Pt(48)
title = p('机器学习小测平台\n设计实现与学习闭环验收', 'Title', False)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
title.paragraph_format.space_after = Pt(20)
sub = p('软件工程课程实验报告', 'Subtitle', False); sub.alignment=WD_ALIGN_PARAGRAPH.CENTER
p('').paragraph_format.space_after=Pt(42)
for text in ['课程名称：________________________', '学生姓名：________________________', '学号与班级：______________________', '共同完成人：______________________', '指导教师：________________________']:
    para=p(text,indent=False); para.alignment=WD_ALIGN_PARAGRAPH.CENTER; para.paragraph_format.space_after=Pt(16)
p('').paragraph_format.space_after=Pt(20)
for text in ['报告日期  2026年11月19日', '源码基线  21c58b0', '验收对象  当前平台及隔离测试数据']:
    para=p(text,indent=False); para.alignment=WD_ALIGN_PARAGRAPH.CENTER

# 2 Overview
page('摘要与阅读说明')
p('本实验围绕机器学习课程的小测与复习，完善一个同时服务教师和学生的平台。系统将课程资料解析、题库管理、考试发布、学生作答、自动评分、学情诊断、资源推荐和针对性复测连接起来，使考试结果能够继续指导学习活动。前端采用 Vue 3，后端采用 FastAPI，使用 SQLite 保存业务记录，并通过 LangGraph 编排诊断与复测流程。')
p('本报告依据当前源码、2026年11月18日的学习闭环专项记录和11月19日的最终验收记录编写。最新验收使用两个测试学生和隔离数据库，验证了考试、诊断、课程资料阅读、备用复测及反馈更新；英文资料导入后进入第二份试卷，并形成同一学生的多卷记录。后端215项、前端38项自动化测试通过，类型检查及前端生产构建通过。')
p('验收同时发现并保留了真实边界：最新复测含重复题，虽然全部答对，也仅显示练习完成；部分历史记录的章节关联仍需人工核对；外部网站的正文可读性不能用HTTP成功响应代替。系统已具备可操作的学习闭环，但短测成绩不能作为长期学习效果的证明。')
p('关键词：机器学习小测；学情诊断；LangGraph；学习证据；复测；软件验收', indent=False)
h('报告结构')
table(['内容', '阅读重点'], [
    ['第1至3章', '实验目标、需求、环境和总体架构'],
    ['第4至7章', '数据模型、英文资料、题库考试与评分'],
    ['第8至13章', '三类诊断、资料推荐、复测规则和界面'],
    ['第14至17章', '章节修复、真实流程、自动化与边界验证'],
    ['第18至19章', '局限、改进、实验总结与复现'],
    ['附录', '源码、证据、提示词及提交前补充事项'],
], [1.45, 5.2])
p('本报告中的运行截图均来自真实浏览器，原图保存在 docs/acceptance/screenshots。章节修复数量按数据库全部历史版本行统计；测试账号的刻意错答和参考答案只用于检验软件行为，不属于真实教学研究数据。', indent=False)

# 3
page('1 实验目标与需求分析')
h('1.1 任务背景')
p('传统的小测系统通常止于展示分数。学生即使知道自己答错，也可能不清楚错误属于哪个章节、应该阅读什么资料，以及复习后是否能够解决同类新问题。因此，本实验把一次考试视为学习过程的起点：先从作答记录识别薄弱环节，再提供有针对性的资料和新题，最后根据复测证据更新状态。')
p('课程任务要求同时利用基础题库和AI题目，支持题目、试卷、多份试卷三个分析层次。其中，多份试卷包括同一学生的多次考试和同一考试的多名学生。实现范围还包括学习内容库、流程技能封装，以及实验报告、演示材料和实际提示词记录。本次工作属于已有系统的完善，不迁移技术栈，也不以重新开发替代修复。')
h('1.2 角色与核心需求')
table(['角色', '主要操作', '期望结果'], [
    ['教师', '导入资料、审题、组卷和发布', '资料能形成可追溯的正式题目和考试'],
    ['教师', '查看单卷、多卷、班级诊断', '定位个体差异与共性薄弱章节'],
    ['学生', '作答、交卷、查看错题', '分数与评分依据一致且可刷新查看'],
    ['学生', '学习资料、发起与继续复测', '始终有下一步动作，结果真实更新'],
    ['双方', '查看复测结果和历史', '共享相同判定规则，既有成果不丢失'],
], [.75, 2.65, 3.25])
h('1.3 可验收的非功能需求')
p('界面以1366×768桌面分辨率为主要基准，核心状态和复测按钮集中展示，长列表在局部滚动。数据修改前备份，验收数据与原有课程数据隔离；提交后刷新及服务重启不应丢失记录。学生只能操作自己的任务和答卷，模型错误或网络失败必须进入可恢复状态，而不能显示模拟成功。')
p('“复测始终可用”在本实验中指入口与恢复动作持续存在，包括开始、续做、查看评分、先练习和重试。它不意味着离线也能调用模型生成新题。对证据不足的结果必须明确保留，不把界面文字美化当作业务修复。')

# 4
page('2 实验环境与实现范围')
table(['层次', '主要组件', '用途与核对依据'], [
    ['前端', 'Vue 3、TypeScript、Vite', '组件化页面；依赖见frontend/package.json'],
    ['交互与展示', 'Element Plus、ECharts、KaTeX', '表单、图表与公式展示'],
    ['后端', 'Python、FastAPI、Pydantic', '鉴权接口、参数校验和业务入口'],
    ['持久化', 'SQLAlchemy、SQLite', '业务实体、事务、历史记录与本地验收'],
    ['工作流', 'LangGraph、后台worker', '多步骤诊断、复测与任务恢复'],
    ['资料处理', 'PyMuPDF、python-docx等', '解析课程文件并保留来源'],
    ['模型适配', 'HTTP模型接口与运行日志', '生成、解释、独立验答；历史实调有deepseek-flash记录'],
    ['验收环境', 'Windows、Chromium', '主要截图1366×768；服务端口8020'],
], [1.05, 2.35, 3.25])
p('表中列出项目依赖与职责，不将依赖文件中的版本范围视为当前机器的精确安装版本。后端依赖入口为 backend/requirements.txt，锁定列表见 backend/requirements.lock.txt；前端应结合锁文件安装。模型凭据保留在本机环境配置中，报告不包含密钥、登录会话或密码。')
h('2.1 数据与实验对象')
p('已有课程资料和匿名课堂小测数据用于构建课程内容与历史分析背景。最新验收另用STU001、STU002两个测试身份，数据库位于 data/final-acceptance/db/mlquiz.sqlite3，从上一轮匿名验收库复制。新增考试、作答及复测均指向这个隔离目录，原有正式作答未被测试输入覆盖。')
h('2.2 版本与结果边界')
p('报告源码基线为21c58b0，业务结果截止2026年11月19日。11月18日的正式复测专项与11月19日的最终数据修复验收分别引用，不能混成一次全部成功的运行。较早报告、演示文稿和压缩包可能包含旧测试数量，应以本报告所列证据时间和当前源码为准。')
p('题库普通组卷保留题库优先、缺题再补充生成的策略；针对薄弱点的专项复测采用AI优先并验证，失败后使用题库备用。两者目的不同，报告分别说明，避免把不同入口的选题方式写成同一规则。')

# 5
page('3 系统总体设计')
architecture()
h('3.1 分层职责')
p('前端负责展示状态、收集答案和发起操作；后端负责权限校验、读取真实业务记录、计算统计与学习结果。LangGraph把诊断、推荐、生成和发布连接为有明确节点的流程。后台任务处理耗时操作，使模型调用不必阻塞一个长时间的浏览器请求。数据库保存答卷和任务状态，页面刷新后从服务端重新获取。')
h('3.2 核心业务链')
p('完整链条为“资料导入→题目提取与审阅→题库入库→组卷发布→学生作答→评分→诊断→资料学习→复测→反馈更新”。其中，资料来源、题目版本、考试版本、答卷和复测轮次相互关联。只要中间某一步使用了无关演示数据，整条链就不能被认定为闭环完成。')
p('AI负责产生候选、解析和解释，是否达到复测标准由程序根据评分及题目证据计算。这样即使模型输出了过于乐观的评语，也不能直接把学生标成已掌握。教师和学生共用后端结果，避免前端在不同页面自行推断造成状态分歧。')

# 6
page('4 数据模型与可追溯关系')
table(['实体或记录', '主要内容', '在闭环中的作用'], [
    ['Document / MaterialUpload', '原文件、来源、解析任务', '核对题目来自哪份资料'],
    ['Chapter / KnowledgePoint', '课程章节、知识点关联', '连接题目、诊断和学习资源'],
    ['Question', '题干、答案、主知识点、版本', '正式题库及修改历史'],
    ['Paper / PaperItem', '试卷与题目快照、分值', '固定学生实际看到的内容'],
    ['AssessmentVersion', '课程考试版本', '保证多人比较指向同一考试'],
    ['AssessmentRelease / Recipient', '发布对象与学生任务', '区分教师发布和个人自测'],
    ['Attempt / Answer / AnswerScore', '答卷、作答内容、评分', '统计和学习证据的事实来源'],
    ['TargetedRetestCycle', '轮次、题源、证据清单', '连接原卷、复测和达标记录'],
    ['Job / SkillRun / PromptRecord', '任务状态、技能、模型记录', '恢复执行与追踪真实调用'],
], [2.2, 2.05, 2.4])
h('4.1 版本与快照')
p('题库中修改题目时产生新版本，已发布试卷的题项保留原内容快照。否则教师修正一道题，就可能改变过去学生参加的考试，导致历史成绩无法解释。章节手工调整也沿用版本机制，不用覆盖原题的方式追求表面一致。修复历史关联时只补能够确定的关系，并保留审计，不改写原始分数。')
h('4.2 主知识点与辅助标签')
p('主知识点表示题目实际考查的对象，是章节统计和复测证据归属的依据。辅助标签只能帮助检索或说明关联，不能因题干偶然出现某个词就认定考查了该知识点。复测证据还需记录学生是否见过题目、题族和考查方式，以便区分新情境迁移与对原题答案的记忆。')
h('4.3 状态与持久化')
p('任务状态回答“现在能做什么”，学习结果回答“已有证据支持什么”。新任务尚未作答、等待评分或本轮未覆盖某知识点时，不能覆盖以前有效的达标记录。持久化验证同时核对数据库、接口和页面，并在服务重启后再次读回；只看到一次成功提示不足以证明记录已经保存。')

# 7
page('5 资料导入与英文试卷处理')
p('导入流程首先保存原文件并计算内容标识，随后识别资料类型、解析页码与题目结构，再生成可供审阅的中文内容。来源与题目记录保持关联；相同文件应复用已有处理结果，失败题目可以单独重试。生成答案和学生实际查看答案属于不同事件，查看行为需要被记录，以免后来将已看答案的题目当成独立复测证据。')
h('5.1 本次英文资料实验')
p('输入为已有UT Austin英文期中练习卷。实际解析4页，提取8题，生成8道中文译文及4页中文PDF。课程范围检查后，2题可发布，另外6题保留为拓展内容。另有课件处理路径产生的3道衍生题，它们与原卷提取题分开记录，没有冒称为原卷题。')
fig('13_imported_exam.png', '英文资料导入与衍生题进入学生第二份测验')
h('5.2 分类错误与修复')
p('英文试卷中的数学公式和短行较多，原有特征曾把它误判为课件。修复时增加英文考试说明、分值标识和小题号等特征，重新识别为考试试卷。这个问题说明，文件格式相同并不意味着内容类型相同；分类应结合语义结构，且保留教师审阅和纠正入口。导入、组卷和发布在本轮通过真实鉴权接口完成，不描述成全部手工点击。')

# 8
page('6 题库管理与考试发布')
p('题库连接课程资料与考试。教师需要看到题干、答案、课程范围、章节和知识点，确认内容适合当前教学后再用于正式试卷。普通组卷优先选择已有可靠题目，题量不足时再进入补充生成；专项复测采用另一套针对薄弱点的新题策略。生成内容通过校验不代表不再需要教师审阅。')
fig('02_question_bank.png', '两列题库及章节关联展示')
h('6.1 考试发布与学生任务')
p('教师选题形成试卷后，发布记录把考试版本与接收学生关联。学生进入个人任务才能建立对应答卷，避免把公共试卷、个人练习和班级考试混在一起。对已经发布的考试，题目快照提供稳定的评分依据；后续题库调整不会悄悄修改学生此前所见内容。')
h('6.2 本次发布结果')
p('教师通过鉴权接口组建一份4题、100分的考试，向STU001和STU002共同发布，两人随后在浏览器参加。第二份考试包含2道导入题及3道衍生题，共5题、50分，由学生A完成。两份试卷通过同一学生身份进入历史诊断，第一份试卷则承担多人单卷和后续复测验证。教师页面真实显示发布记录。')

# 9
page('7 学生作答与评分反馈')
p('学生在浏览器逐题填写答案，保存后确认交卷。服务端依据试卷快照和有效评分结果形成总分、错题和待评分状态。评分未完成时不能用零分代替，也不能将未知对错纳入达标证据。参考答案、解析、答卷和评分应能相互追踪，以便学生提出疑问时检查具体题目。')
fig('04_student_exam.png', '学生A在真实考试中逐题作答')
h('7.1 实验输入与输出')
p('第一份考试中，两名学生各有意答错一道题，均获得75/100；错题用于验证诊断和推荐，不代表自然发生的学习困难。学生A完成第二份考试得到50/50。分数由正常交卷后的评分流程产生，没有通过SQL直接写入成绩，也没有伪造评分响应。数据库读回的第一份考试成绩与接口一致。')
h('7.2 指标含义')
p('得分率适合展示一份试卷的总体表现，章节和知识点正确情况用于定位具体问题。二者不能互相替代：一份试卷总分较高，仍可能在某一重要知识点出错；全部未评分也不应显示为全部答错。本实验把作答、评分进度与学习判定分开展示，以免用户把流程尚未完成误解为能力不足。')

# 10
page('8 单人单卷诊断')
p('单人单卷首先限定学生和原始答卷，再读取题项、评分、主知识点及章节。界面以本次得分、章节表现和错题依据呈现薄弱环节，右侧展示当前知识点的学习材料与复测操作。它应直接回答本次哪里不熟悉以及接下来做什么，不要求学生先阅读系统分析方法的长篇说明。')
fig('06_single_diagnosis.png', '教师查看学生A本次考试及后续复测状态')
h('8.1 诊断依据')
p('诊断以实际评分为基础，优先追踪错题对应的主知识点。原卷相关题全部答对只说明本次题目作答正确，不能自动升级为复测意义上的知识点达标；一道正确题也不足以证明能够应对不同情境。对于章节字段缺失的历史题目，需要先修复数据关系，而不是在统计图中随意归入某章。')
h('8.2 复测后的更新')
p('本轮学生A完成5题复测，但其中有重复题，因此教师单卷页保留原卷75分，同时呈现练习完成结果，薄弱知识点不被错误清除。原考试成绩是历史事实，复测结果是新增学习证据，两者都应保留。若后续可靠新题答错，当前状态可以转为需加强，历史通过记录仍然可查看。')

# 11
page('9 单人多卷诊断')
p('单人多卷以学生为中心组织不同考试与复测记录，观察章节表现随时间的变化。每条记录仍保留原卷归属，不能把甲试卷的复测误挂到乙试卷。缺考、待评分或无效作答应按实际状态处理，不把缺失记录计成零分，也不因同一作答被多次读取而重复计入统计。')
fig('07_history_diagnosis.png', '学生A的多卷诊断包含本轮新增记录与此前历史')
h('9.1 本轮验证')
p('学生A完成共同考试75/100和第二份试卷50/50，并保留第一份考试的复测记录。教师历史诊断可读取关联记录，复测状态与学生反馈一致。截图选择全部历史，包含此前验收数据；71%是该范围的平均得分率，不是本轮两份试卷的平均。历史列表采用分页，避免全部记录连续铺放。')
h('9.2 变化解释的边界')
p('第一份考试得分率75%、第二份100%，可以作为两次作答表现的描述，不能直接得出学习能力提高25个百分点的因果结论。两份试卷的题目、难度和覆盖范围不同，测试答案还经过人为设计。更可靠的学习判断应比较相同目标下独立新题的表现，并记录间隔时间、提示使用和题量。')

# 12
page('10 多人单卷诊断')
p('多人单卷限定同一考试版本，再对参加者的成绩、题目错误分布和章节表现进行汇总。统一试卷范围使教师能够定位共性问题，并查看需要帮助的学生。班级诊断还需要展示复测的待完成、待评分、通过和未通过等结果，不能只显示发布了多少个任务。')
fig('08_group_diagnosis.png', '同一份考试中两名学生的班级诊断')
h('10.1 数据关联和身份边界')
p('本轮学生A、B参加同一份4题考试，均得到75分。相同总分并不代表错误内容相同，应继续查看具体题目与知识点。两名测试学生足以检查多人聚合、名单和归属关系，但样本过小，不能据此做稳定的班级教育统计结论。教师具有课程管理视角，学生端只能看到授权给自己的结果。')
h('10.2 双端一致性验证')
p('验收逐项比对学生反馈与教师单卷、多卷、班级三类接口中的复测记录和知识点状态，结果一致。学生B尝试读取学生A反馈返回403，说明被测接口执行了身份隔离。该结果只代表所测访问场景，不等于对整个平台完成了全面安全审计。')

# 13
page('11 薄弱点资源推荐')
p('资源推荐从当前有效薄弱点出发，关联课程章节、知识点和具体内容。默认优先显示课程原页及一个相关外链，其余资料放入抽屉，减少主界面的阅读负担。复测如果暴露了原卷没有暴露的问题，也应刷新对应资料，不能只沿用第一次考试的错题列表。')
fig('09_resource_recommendation.png', '学生B的薄弱知识点与对应学习资料')
h('11.1 可点击与可阅读')
p('本轮学生B实际点击推荐的课程原页，页面图片成功加载，形成“诊断→资料阅读”的证据。课程资料应尽量指向明确页码，视频应尽量指向对应分集；搜索页、话题页或无法对应具体内容的合集不能充当高质量推荐。章节映射错误会直接导致推荐偏离，因而推荐质量依赖前面的数据关系。')
h('11.2 外部平台的处理')
p('此前检查已核对35个知识点的82处课程原页图片引用。外链返回HTTP 200只能说明服务器响应，不等于正文可读；登录弹层、限流和内容下架仍可能阻断学习。已发现受限的抖音链接从默认推荐移除，保留课程原页替代。本轮没有重新逐条阅读所有外链，报告不写成全部链接已验证。')

# 14
page('12 复测规则与学习证据')
p('复测的核心不是再出几道题，而是明确怎样的结果支持怎样的结论。系统采用learning-loop-v3规则版本，将整轮结果与知识点结果分开。每轮通常5题，最多覆盖2个知识点；两个目标按3加2分配，一个目标则使用全部5题。选题结合最新有效证据和覆盖次数，避免永远重复原卷排在前面的薄弱点。')
table(['结果层次', '验收规则', '不能据此推断'], [
    ['本轮复测通过', '正式5题全部可靠评分，至少答对4题', '所有知识点都已达标'],
    ['知识点达标', '同轮该主知识点至少2道独立新题；不同题族及考查方式；全部答对', '已经永久掌握或能解决所有难题'],
    ['延后复测确认', '达标至少24小时后开始另一组独立新题，再次达到知识点标准', '未来不会遗忘'],
    ['练习完成', '重复题、提示使用或不足量等练习完成', '可以升级知识点达标状态'],
], [1.1, 3.45, 2.1])
h('12.1 典型边界案例')
p('如果两知识点分别分配3题和2题，学生总共答对4题，整轮可以通过；但包含错题的知识点仍需加强。若一个知识点只答对1题，证据数量不足。若题目没有完成可靠评分，则不能把暂未发现错题等同于全部正确。只改变数字或措辞的题目也不应轻易当成不同题族。')
h('12.2 跨轮状态更新')
p('共享服务learning_evidence.py按轮次汇总有效证据。发布新任务、部分待评分和本轮未覆盖，不撤销已有达标；可靠错答则更新当前状态，并保留过去的通过记录。延后确认比较新一轮开始时间与此前达标时间，不能通过提前开始、晚些提交来伪造24小时间隔。历史记录缺少新题或独立性信息时保持证据不足。')
p('这些标准是本项目可检查的工程规则，不是已被本实验验证的教育测量定律。更严格的长期掌握评价需要更多题目、时间间隔和真实学生样本。')

# 15
page('13 复测工作流与界面交互')
h('13.1 LangGraph与技能职责')
p('共享工作流依次执行diagnose、recommend_resources、generate_retest_candidates、validate_and_publish_retest。状态中携带请求、操作者、范围计划、候选、输出、错误和阶段记录。diagnose_classroom、recommend_resources等技能封装可审计的输入输出；verify_learning使用共享学习证据规则。技能运行并不都需要模型调用，确定性计算不能伪装成AI生成。')
h('13.2 题源降级与恢复')
p('AI未配置、超时或候选验答不通过时，从同目标、可靠、学生未见过的题库题补足。仍不足时转为练习，明确记录AI、题库和重复题数量，保留重新生成入口。已有未完成正式复测时优先续做；教师与学生重复发起应复用同范围任务。持久化任务和数据库事务承担恢复与去重，不能只依赖内存中的工作流状态。')
fig('10_retest.png', '学生A打开针对性复测任务并作答', width=6.3)
h('13.3 保持简洁且有下一步')
p('两端采用浅蓝白色工作台，顶部保留核心结果，左侧选择对象，右侧显示详情；建议、依据和历史通过标签或抽屉切换。复测区根据状态显示开始、继续、查看评分或再次复测，成功后仍保留入口。精简的是重复说明，不删除重要错误、证据不足和失败原因。长题目、记录及表格允许局部滚动。')

# 16
page('14 章节映射问题与数据修复')
h('14.1 根本原因')
p('“未映射章节”并非单纯显示问题。部分题目只有主知识点而没有chapter_id；章节名称存在“第5章 SVM”和“第5章 支持向量机”等别名；课程目录还缺少已有资料中的绪论、实验或框架章节。历史题项与评分记录没有完整继承章节，导致同一题在题库、诊断和推荐中表现不一致。')
h('14.2 修复方法')
p('先通过SQLite backup保存备份，再优先按主知识点所属课程章节匹配；完整标题无法唯一匹配时使用章节编号；仍无关联时只接受唯一明确的题干主题。涉及多个章节或主题不明的记录留待人工核对，已有明确关联不覆盖。自动映射依据写入题目备注及修复审计，不能把全部缺失项强行归入同一章。')
table(['表', '总记录', '修复前缺失', '已补齐', '修复后缺失'], [
    ['questions', '852', '556', '504', '52'],
    ['paper_items', '509', '365', '311', '54'],
    ['answer_scores', '773', '773', '595', '178'],
], [1.65, 1.0, 1.4, 1.2, 1.4])
p('统计口径为修复时数据库的全部版本行，不是前端当前有效题目数；也不是最新新增考试之后的实时全库总量。题目缺失率由约65.3%降至6.1%，但不能据此宣称全库映射完成。原始题干、快照内容和分数未因这次关系补齐而被改写。')
h('14.3 防止问题再次出现')
p('在SQLAlchemy会话写入阶段，对新建或更新且缺章节的Question、PaperItem和AnswerScore使用同一映射服务，补齐能够确定的关联。教师题库详情增加“调整章节”和“保存章节”，实际点击已验证，人工修改产生新版本。待审记录输出为mapping_review.csv，历史答卷需结合课程原资料核对后再做有审计的修复。')
h('14.4 修复后的联动验证')
p('本次新考试的题项均有关联章节，数据库成绩与接口一致，诊断可按章节汇总，薄弱点能够继续关联课程资料和复测。说明验证应沿题库到评分记录逐层检查，不能只在题目列表上看见章节名称就认定修复完成。')

# 17
page('15 双学生真实流程与实验结果')
table(['阶段', '操作方式', '实际结果'], [
    ['教师准备', '教师身份调用真实接口', '4题考试发布给A、B；教师页面可见'],
    ['学生A首卷', '浏览器作答、保存、交卷', '75/100，产生可追溯错题'],
    ['学生B同卷', '浏览器作答、保存、交卷', '75/100，形成多人单卷样本'],
    ['资源学习', 'B点击推荐课程原页', '图片加载成功，保留截图'],
    ['A发起复测', '诊断范围内自主发起', 'AI候选未合格，进入题库备用'],
    ['A完成复测', '浏览器正常提交5题', '5/5，1道未见题加4道重复题'],
    ['A第二份卷', '导入及衍生题组卷后作答', '50/50，进入个人多卷历史'],
    ['最终复核', '刷新、API、SQLite及重启', '成绩和复测状态保留，双端一致'],
], [1.15, 2.35, 3.15])
fig('11_progress_comparison.png', '复测全对仍显示练习完成并保留薄弱点', width=6.3)
p('本次完整走通的是“考试→诊断→资料→备用复测→提交→反馈更新”，以及“英文导入→题库→第二份考试→多卷记录”。最新运行没有获得一次新的正式AI新题达标证据，因此不能写成所有AI路径在本轮均成功。A的复测10/10只说明本轮练习作答正确，系统没有据此清除薄弱点。')

# 18
page('16 自动化测试与分层验收')
h('16.1 自动化结果')
table(['检查', '最新结果', '说明'], [
    ['后端测试', '215项通过', '保存日志；耗时84.77秒'],
    ['前端单元测试', '38项通过', '失败0项、跳过0项'],
    ['TypeScript', '检查通过', '类型检查不替代运行时验证'],
    ['Vite', '生产构建通过', '构建成功不等于业务全通过'],
    ['关键浏览器页面', 'pageerror为0', '限本轮实际访问页面'],
    ['布局检查', '1366×768核心页通过', '教师四个主要页面无整页滚动'],
], [1.5, 1.65, 3.5])
h('16.2 为什么要分层验证')
table(['验证层', '需要回答的问题', '本次证据'], [
    ['页面', '能否展示、能否实际操作', '浏览器登录、作答、资料、章节调整及截图'],
    ['接口', '请求是否触发真实业务', '导入、发布、提交、诊断及复测返回记录'],
    ['持久化', '结果是否真正保存', 'SQLite读回四份新增答卷；重启后复核一致'],
    ['闭环', '前后是否关联同一人和任务', '原卷、错题、资料、复测及反馈串联'],
], [.85, 2.45, 3.35])
p('这四层不能互相替代。页面打开只证明渲染可用；接口返回成功也可能伴随错误归属或事务未落库；数据库有记录仍可能与用户刚才操作的试卷无关。因此验收同时追踪学生、试卷、题目来源、答卷和复测轮次，并比对原始成绩与三类诊断结果。')
h('16.3 布局与显示结论')
p('最新截图以1366×768为准。此前专项还检查1280×720与600×800窄屏；125%场景采用等效CSS视口检查，未把它写成工具准确测得的真实缩放比例。主要结果和复测入口固定，内容区域局部滚动；字体不通过过度缩小来换取一屏显示。')

# 19
page('17 正式复测专项与边界验证')
p('为避免最新备用练习结果遮盖已有正式复测证据，本章单独引用11月18日学习闭环专项记录[2]。该专项使用另一隔离目录，记录23条真实模型调用，涵盖生成与独立验答。它证明被测正式路径曾经成功执行，不代表模型此后每次都能产生合格题目。')
table(['场景', '实际或测试结果', '验证的业务规则'], [
    ['此前学生A正式轮', '3道AI新题加2道题库题；5/5；两个知识点达标', '混合题源在独立证据充足时可判达标'],
    ['此前学生B正式轮', '题库5题；4/5整轮通过；一知识点仍需加强', '整轮通过不等于全部知识点达标'],
    ['发布下一轮', '此前通过与达标保留', '任务状态不能抹掉学习结果'],
    ['双方同时发起', '教师与学生返回同一job_id', '同学生同范围请求去重'],
    ['访问他人记录', '被测答卷403、任务404', '不暴露其他学生的私人结果'],
    ['重启后继续', '历史和未完成任务读回一致', '恢复依赖持久化业务状态'],
], [1.3, 3.0, 2.35])
h('17.1 单元与边界场景')
p('自动化检查覆盖单题全对、部分待评分、重复题、提示使用、同题族、同考查方式、辅助标签、旧数据证据不足和24小时时间边界；还覆盖7个目标的轮转、无历史基线、模型未配置、超时、验答失败、题库不足及worker中断恢复。模拟异常用于检验分支行为，不能计作真实模型调用。')
h('17.2 发现问题的方法')
p('只运行旧测试可能继续得到全部通过，因为旧断言并未涉及新发布任务是否覆盖历史状态。补充“第一轮通过→发布第二轮→尚未作答”的场景，才能检验学习成果是否被错误撤销。本实验采用先追踪完整流程、再构造边界条件的方法，使验证对象从函数有没有返回扩展到业务含义是否正确。')

# 20
page('18 局限分析与后续改进')
h('18.1 章节关联仍需人工核对')
p('52条题目版本、54个历史题项和178条评分记录仍缺少可唯一确定的章节。下一步应按待审清单对照原资料，优先处理实际考试和常用题目；教师确认后保存新版本或执行有审计的历史关联修复。仅增加模糊关键词可能提高覆盖率，也可能把跨章节题错误归类，因此不应只追求缺失数量为零。')
h('18.2 独立新题供给仍不稳定')
p('最新真实运行中AI候选没有达到要求，题库也无法提供足量未见题，最终进入重复练习。这是功能可恢复但证据供给不足的情况。后续应为每个常用知识点积累不同题族、题型和情境，记录候选拒绝原因，重点改进验答一致性和目标覆盖。不能为了提高通过率放松独立性标准。')
h('18.3 内容范围与外部资源')
p('英文资料中6道拓展题需教师审查课程适用性；翻译与AI解析也不能被当作权威标准答案。外链可能需要登录、限流或下架，推荐应保留检查时间、正文可读状态和本地替代。可优先维护少量明确对应章节的高质量内容，再定期复核，不靠堆积链接制造丰富感。')
h('18.4 教学效果与安全范围')
p('两名测试学生和参考答案输入只能验证软件流程，不能证明真实学习效果。后续若要评估教学价值，应在合适授权和匿名化条件下收集更多真实作答，比较相同知识目标下的独立题表现。当前本地演示登录不等于生产级身份体系；正式部署还需完善账号、会话、权限、审计与备份策略。')
h('18.5 交付和历史材料')
p('当前工作区包含最新隔离验收数据，但它不是全部随Git提交的数据库快照。只克隆代码不能保证直接复现带固定案例编号的最终读回脚本；需要匹配的数据目录，或重新创建隔离案例。旧压缩包不会自动包含新修改。成员信息、指定开发平台使用凭证和缺失的早期提示词需由实际完成人补充，报告不补造经历。')

# 21
page('19 实验总结与复现方法')
h('19.1 实验总结')
p('本实验的主要成果是把已有功能连接为可追溯的业务流程，并改正影响结论的状态和数据问题。章节修复恢复了题目、评分、诊断和资料之间的关联；共享证据服务使教师和学生对同一次复测得到一致结论；备用题源和持续入口让失败后的学习流程能够继续。界面精简集中核心操作，没有用删除错误信息掩盖问题。')
p('从软件工程角度，最值得总结的是三点。第一，版本快照保护历史事实，避免后续编辑改变过去考试。第二，任务进度与学习结果属于不同维度，应该分别建模。第三，验收既要检查正常路径，也要故意构造重复题、待评分和新任务覆盖历史等边界。自动化测试负责稳定复核，真实浏览器与数据库读回负责证明功能确实连接起来。')
h('19.2 本机复核步骤')
p('在项目根目录、已安装项目依赖且保留匹配验收数据的前提下，设置MLQ_DATA_DIR为data/final-acceptance，再启动后端与worker。两个终端必须指向同一数据目录，否则页面和后台任务可能读取不同数据库。以下命令用于已有本机验收环境，不是新机器一键初始化方案。')
for text in [
    "$env:MLQ_DATA_DIR = Join-Path (Get-Location) 'data/final-acceptance'",
    'python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8020',
    '# 另一个终端设置相同 MLQ_DATA_DIR 后执行',
    'python -m backend.app.workers.worker --interval 2',
    '# 使用匹配的隔离案例进行只读核验',
    'python backend/scripts/final_readback.py',
]:
    para=p(text,indent=False); para.paragraph_format.space_after=Pt(3)
    for run in para.runs: run.font.name='Consolas'; run.font.size=Pt(8.7)
p('先备份数据库，再开展带写操作的测试；不要把初始化、发布或批量修复脚本指向正式数据。复核时依次检查考试记录、学生作答、诊断归属、复测题源与结果，再刷新页面和重启服务验证持久化。AI功能需要真实本机配置，没有配置时只能验证备用路径，不能把备用成功写成模型成功。')
h('19.3 学习所得')
p('系统能输出结果，与结果有充分依据，是两件不同的事。把题目来源、评分可靠性、独立性和时间条件保存下来，才能解释为什么通过、为什么未达标以及下一步需要什么证据。这种以事实记录支撑结论的方法，也适用于推荐系统、审批系统和其他包含AI输出的软件。')

# 22
page('附录 证据索引与提交检查')
p('以下路径均相对于项目根目录。编号用于对应正文引用；运行截图来自11月19日，不将系统结构示意图当作运行证据。报告包含9张真实界面截图，完整14张原图及操作图注另存验收目录。', indent=False)
table(['编号', '材料位置', '主要用途'], [
    ['[1]', 'docs/acceptance/final_acceptance.md', '最新修复、215/38测试与真实流程结论'],
    ['[2]', 'docs/LEARNING_LOOP_V3_ACCEPTANCE.md', '此前正式复测、跨轮状态和异常边界'],
    ['[3]', 'docs/acceptance/readback.json\ndocs/acceptance/run.json', '数据库与接口结果、测试对象关联'],
    ['[4]', 'docs/acceptance/chapter_mapping.json\nchapter_mapping_followup.json', '两轮映射修复统计；第二文件同目录'],
    ['[5]', 'docs/acceptance/import-run.json\nimported-questions.json', '英文解析与题目来源；第二文件同目录'],
    ['[6]', 'docs/acceptance/backend-tests.txt\nfrontend-tests.txt', '最新自动化日志；第二文件同目录'],
    ['[7]', 'docs/acceptance/screenshots/\ndocs/acceptance/README.md', '真实原图、步骤与图注'],
    ['[8]', 'docs/evidence/learning-loop-v3/\nruntime-prompts.json', '此前23条真实调用；文件路径分行显示'],
    ['[9]', 'docs/PROMPT_HISTORY.md', '提示词记录范围与缺失历史说明'],
], [.55, 3.7, 2.4])
h('关键实现位置')
p('backend/app/services/learning_evidence.py集中学习证据判定；services/learning_loop.py安排目标与任务；graphs/learning_loop.py编排复测。services/chapter_mapping.py与scripts/repair_chapter_mapping.py负责新增数据关联和历史修复。上述后端路径均以backend/app或backend为前缀。数据库实体见backend/app/models/entities.py，双端页面见frontend/src/views。', indent=False)
h('提交前需由本人补充')
p('填写封面的真实姓名、学号、班级、教师和共同完成人；根据实际情况说明成员分工。若课程要求指定开发平台的使用记录，应提交对应真实材料，不能用本报告替代。正文已区分最新验收与历史专项；若后续修改源码或补充人工映射，应同步更新实验结果，不沿用过时数量。', indent=False)

path = OUT / 'course_report_detailed.docx'
doc.save(path)
plain='\n'.join(x.text for x in doc.paragraphs)
stats={'output':str(path),'figures':figure_count,'manual_pages':22,'paragraphs':len(doc.paragraphs),'tables':len(doc.tables),'characters_prose':len(plain),'characters_total':len(plain)+sum(len(c.text) for t in doc.tables for r in t.rows for c in r.cells)}
import json
(QA/'build-stats.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(stats,ensure_ascii=False))

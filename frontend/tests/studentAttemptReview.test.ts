import test from 'node:test'
import assert from 'node:assert/strict'
import {
  choiceMark, itemResult, mathReadyAnswer, readableAnswer, referenceAnswer, studentAnswer, truthMark,
} from '../src/utils/studentAttemptReview.ts'

test('多选题明确区分选对、漏选、错选，映射不可靠时不猜漏选', () => {
  const item = {
    position: 5, question_type: 'multi_choice', score: { correct: false },
    selection_status: 'reliable', selected_keys: ['A', 'C'],
    reference_available: true, reference_keys: ['A', 'B'],
  }
  assert.deepEqual(choiceMark(item, 'A'), { tone: 'correct', label: '已选 · 参考答案' })
  assert.deepEqual(choiceMark(item, 'B'), { tone: 'missed', label: '漏选 · 参考答案' })
  assert.deepEqual(choiceMark(item, 'C'), { tone: 'wrong', label: '学生错选' })
  assert.deepEqual(choiceMark(item, 'D'), { tone: '', label: '' })
  assert.equal(choiceMark({ ...item, selection_status: 'unmapped' }, 'B').tone, 'reference')
  assert.equal(itemResult({ ...item, score: null }), 'ungraded')
})

test('判断题标记和未评分结果保持独立', () => {
  const item = {
    position: 1, question_type: 'true_false', score: { correct: false },
    student_boolean: false, reference_boolean: true, reference_available: true,
  }
  assert.equal(truthMark(item, false).tone, 'wrong')
  assert.equal(truthMark(item, true).tone, 'missed')
  assert.equal(itemResult({ ...item, score: { correct: null } }), 'ungraded')
})

test('全错、部分得分与完全未评分分别归入正确的区域', () => {
  const scores = [
    { correct: false, awarded_score: 0, max_score: 5 },
    { correct: false, awarded_score: 2, max_score: 5 },
  ]
  assert.deepEqual(scores.map(score => itemResult({ position: 1, question_type: 'numeric', score })),
    ['wrong', 'wrong'])
  assert.equal(itemResult({ position: 1, question_type: 'short_answer', score: null }), 'ungraded')
})

test('历史结构化作答仅显示答案内容，不暴露内部字段', () => {
  assert.equal(readableAnswer({ answered: true, texts: ['第一个选项', '第二个选项'], raw_text: '原文' }),
    '第一个选项；第二个选项')
  assert.equal(readableAnswer({ answered: true, value: false }), '错误')
  assert.equal(readableAnswer({ text: '先求梯度，再更新权重。' }), '先求梯度，再更新权重。')
  assert.equal(readableAnswer({ value: 2.5, unit: '米' }), '2.5 米')
  assert.equal(readableAnswer({ values: [{ accepted: ['3', '三'] }, { accepted: ['4'] }] }), '3 / 三；4')
  assert.equal(readableAnswer({ answered: false, raw_text: '旧数据' }), '未作答')
  assert.equal(readableAnswer({ answered: true, debug: { source: 'import' } }), '未作答')
  const item = { position: 2, question_type: 'multi_choice',
    selection_status: 'reliable', selected_keys: ['A', 'C'],
    reference_available: true, reference_keys: ['A', 'B'] }
  assert.equal(studentAnswer(item), 'A、C')
  assert.equal(referenceAnswer(item), 'A、B')
})

test('无分隔符的历史 LaTeX 答案交给现有 KaTeX 渲染', () => {
  assert.equal(mathReadyAnswer('损失是：\\sum{e^{y H(x)}}。'),
    '损失是：$\\sum{e^{y H(x)}}$。')
  assert.equal(mathReadyAnswer('已有 $\\frac{1}{2}$ 公式'), '已有 $\\frac{1}{2}$ 公式')
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import katex from 'katex'
import { normalizeDisplayMath } from '../src/utils/mathDisplay.ts'

test('已核对的题干与 LDA 解析公式转换为 KaTeX 可渲染表达式', () => {
  const cases = [
    'sigmoid函数g(z)=1/(1+e^(-z))相对变量z求导，其导数为g(z)*(1-g(z))',
    'LDA 常用目标为 J = w^T S_b w / w^T S_w w',
    '在约束 w^T S_w w = 1 下求解',
    'σ′(z)=σ(z)(1−σ(z))',
    '约束 W^TW=I，得到 XX^TW=λW',
    '按XXT的特征值大小降序排列',
  ]
  for (const source of cases) {
    const result = normalizeDisplayMath(source)
    assert.match(result, /\$[^$]+\$/, source)
    for (const match of result.matchAll(/\$([^$]+)\$/g)) {
      assert.doesNotThrow(() => katex.renderToString(match[1], { throwOnError: true }), source)
    }
  }
})

test('已有公式和普通文字保持原样', () => {
  assert.equal(normalizeDisplayMath('已有 $w^T S_b w$ 与普通文字'), '已有 $w^T S_b w$ 与普通文字')
  assert.equal(normalizeDisplayMath('请比较梯度下降与正规方程。'), '请比较梯度下降与正规方程。')
})

test('题干内直接展示原图且学生页面不展示技术详情', () => {
  const source = readFileSync(new URL('../src/components/SourceQuestionContent.vue', import.meta.url), 'utf8')
  const exam = readFileSync(new URL('../src/views/ExamView.vue', import.meta.url), 'utf8')
  const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.match(source, /AuthenticatedImage/)
  assert.doesNotMatch(source, /查看原题资料/)
  assert.doesNotMatch(exam, /TechnicalDetails|评分与答案技术详情/)
  assert.match(app, /brand-mark[^>]*>ML</)
})

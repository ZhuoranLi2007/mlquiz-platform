import test from 'node:test'
import assert from 'node:assert/strict'
import { questionAnswerText, questionRubricText, readableAnswerContent, rubricDisplayText, formatAnswer } from '../src/utils/presentation.ts'

test('题库真实主观题使用reference_answer，支持旧rubric答案', () => {
  assert.equal(questionAnswerText({question_type:'term', answer_payload:{reference_answer:'有限样本下的泛化能力',key_points:['内部要点']}}), '有限样本下的泛化能力')
  assert.equal(questionAnswerText({rubric:{reference_answer:'历史参考答案'}}), '历史参考答案')
})
test('真实AI题points.point与不同评分要点格式均保留正文和分值', () => {
  assert.equal(rubricDisplayText({points:[{point:'说明偏差与方差',score:2}]}), '说明偏差与方差（2分）')
  assert.equal(rubricDisplayText({criteria:[{criterion:'$R(f)$',score:0},{text:'第二点'},{description:'第三点',weight:0.5}]}), '$R(f)$（0分）；第二点；第三点')
})
test('选择、判断错误和数值0均是有效答案', () => {
  assert.equal(questionAnswerText({question_type:'single_choice',answer_payload:{key:'B'}}), '选项 B')
  assert.equal(questionAnswerText({question_type:'multi_choice',answer_payload:{keys:['A','C']}}), '选项 A、C')
  assert.equal(questionAnswerText({question_type:'true_false',answer_payload:{value:false}}), '错误')
  assert.equal(questionAnswerText({question_type:'numeric',answer_payload:{numeric:0,unit:'m'}}), '0 m')
})
test('多空答案、候选答案和公式按内容展示', () => {
  assert.equal(questionAnswerText({answer_payload:{parts:[{label:'第一空',value:0},{accepted:['L1','Lasso']},'$x^2$']}}), '第一空：0；L1；Lasso；$x^2$')
})
test('未知嵌套对象不泄露元数据，空字段继续查找真实答案', () => {
  for (const value of [{internal:{token:'secret'}},{value:{bad:{private:'secret'}}},'[object Object]']) {
    assert.equal(readableAnswerContent(value), '未作答')
    assert.equal(questionAnswerText({answer_payload:value}), '参考答案待补充')
  }
  assert.equal(questionAnswerText({answer_payload:{value:{unknown:1},reference_answer:'有效答案'}}), '有效答案')
  assert.equal(rubricDisplayText({points:[{internal:{private:'secret'}}]}), '')
})
test('缺失主观题规则不声称自动评分，空答案保留真实提示', () => {
  assert.equal(questionAnswerText({answer_payload:{}}), '参考答案待补充')
  assert.equal(questionRubricText({question_type:'term',answer_payload:{reference_answer:'已知答案'}}), '评分要点待补充')
  assert.equal(questionRubricText({question_type:'single_choice',answer_payload:{key:'A'}}), '按参考答案及题目评分设置评分')
  assert.equal(questionRubricText({question_type:'single_choice',answer_payload:{}}), '评分要点待补充')
})

test('考试反馈共用安全格式化，不输出未知字段或对象', () => {
  assert.equal(formatAnswer({reference_answer:'已有参考答案'}), '已有参考答案')
  assert.equal(formatAnswer({value:0}), '0')
  assert.equal(formatAnswer({internal:{private:'secret'}}), '未作答')
})

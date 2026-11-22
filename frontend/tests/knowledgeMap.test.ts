import test from 'node:test'
import assert from 'node:assert/strict'
import { chapterGroups, knowledgeLabel, knowledgeState } from '../src/utils/knowledgeMap.ts'

test('地图只采用后端掌握结论，满分与未知状态均不自动升级', () => {
  for (const state of [undefined, 'passed', 'unrecognized']) {
    assert.equal(knowledgeState({ statistics: { accuracy: 1 }, learning_result: { state } }), 'insufficient')
  }
  assert.equal(knowledgeLabel({ learning_result: { state: 'delayed_confirmed' } }), '延后确认')
})

test('章节筛选保留独立概念ID并支持中文搜索和空结果', () => {
  const concepts = [
    { id: 'cv:precision', chapter: '模型评估', name: '查准率', learning_result: { state: 'needs_review' } },
    { id: 'cv:recall', chapter: '模型评估', name: '召回率', learning_result: { state: 'achieved' } },
    { id: 'unknown', name: '历史概念' },
  ]
  assert.equal(chapterGroups(concepts).length, 2)
  assert.equal(chapterGroups(concepts, '查准')[0].items[0].id, 'cv:precision')
  assert.equal(chapterGroups(concepts, '', 'achieved')[0].items[0].id, 'cv:recall')
  assert.deepEqual(chapterGroups(concepts, '不存在'), [])
  assert.equal(chapterGroups(concepts)[1].chapter, '章节待确认')
})

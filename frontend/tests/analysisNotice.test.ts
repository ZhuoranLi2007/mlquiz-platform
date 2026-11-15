import test from 'node:test'
import assert from 'node:assert/strict'
import { analysisNotice } from '../src/utils/analysisNotice.ts'

test('quality fallback is not described as an unavailable AI service', () => {
  assert.match(analysisNotice({ model_status: 'invalid_output' }), /内容校验/)
  assert.doesNotMatch(analysisNotice({ model_status: 'invalid_output' }), /服务暂时不可用/)
  assert.equal(analysisNotice({}), '')
})

test('configuration and connection failures have distinct notices', () => {
  assert.match(analysisNotice({ model_status: 'model_not_configured' }), /尚未配置/)
  assert.match(analysisNotice({ model_status: 'network_error' }), /连接未成功/)
})

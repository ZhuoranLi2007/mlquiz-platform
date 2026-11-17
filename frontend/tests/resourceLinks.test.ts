import test from 'node:test'
import assert from 'node:assert/strict'
import { recommendedResourceLinks, resourceHref } from '../src/utils/resourceLinks.ts'

test('recommendation links preserve all knowledge-point resources and remove duplicate or unsafe URLs', () => {
  const resources=Array.from({length:8},(_,i)=>({url:`/learn/kp.${i}`,kp_id:`kp.${i}`,location:`第${i+1}页`}))
  const result=recommendedResourceLinks([...resources,resources[0],{url:'javascript:alert(1)'},{url:'//example.com'}])
  assert.equal(result.length,8)
  assert.equal(result[7].location,'第8页')
  assert.equal(resourceHref('javascript:alert(1)'), '')
  assert.equal(resourceHref('/\\evil.example'), '')
})

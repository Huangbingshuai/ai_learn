import assert from 'node:assert/strict'
import test from 'node:test'
import { resultTier } from '../src/features/result-style'

test('selects feedback from exact accuracy thresholds for both 12 and 20 question sets', () => {
  assert.deepEqual([0, 2, 3, 5, 6, 8, 9, 11, 12].map((n) => resultTier(n, 12)),
    ['explore', 'explore', 'discover', 'discover', 'progress', 'progress', 'excellent', 'excellent', 'perfect'])
  assert.deepEqual([4, 5, 10, 15, 20].map((n) => resultTier(n, 20)),
    ['explore', 'discover', 'progress', 'excellent', 'perfect'])
  assert.equal(resultTier(0, 0), 'explore')
})

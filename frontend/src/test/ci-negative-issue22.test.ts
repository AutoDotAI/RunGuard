import { expect, test } from 'vitest'

test('intentional CI failure for issue 22', () => {
  expect(1).toBe(2)
})

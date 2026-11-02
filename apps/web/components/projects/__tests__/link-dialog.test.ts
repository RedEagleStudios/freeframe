import { describe, expect, it } from 'vitest'
import { normalizeLinkUrl } from '../link-dialog'

describe('normalizeLinkUrl', () => {
  it('keeps a full web address as typed', () => {
    expect(normalizeLinkUrl(' https://outline.example.com/doc/script-abc ')).toBe(
      'https://outline.example.com/doc/script-abc',
    )
    expect(normalizeLinkUrl('http://10.0.0.5:3000/doc/x')).toBe('http://10.0.0.5:3000/doc/x')
  })

  it('adds https:// to an address pasted without one', () => {
    expect(normalizeLinkUrl('outline.example.com/doc/x')).toBe('https://outline.example.com/doc/x')
  })

  it('refuses anything a new tab would run instead of open', () => {
    expect(normalizeLinkUrl('javascript:alert(1)')).toBeNull()
    expect(normalizeLinkUrl('data:text/html,<b>x</b>')).toBeNull()
    expect(normalizeLinkUrl('mailto:someone@example.com')).toBeNull()
    expect(normalizeLinkUrl('   ')).toBeNull()
  })
})

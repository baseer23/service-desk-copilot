import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import InvestigationHistory, { caseStatus, sampleCase } from './InvestigationHistory'
import type { Case } from './App'

function item(id: string, summary: string, extra: Partial<Case> = {}): Case {
  return {
    case_id: id,
    thread_id: id,
    ticket: { summary },
    status: 'needs_input',
    ...extra,
  } as Case
}

test('known samples stay separate, unknown records remain visible, history is searchable and paginated', async () => {
  const onOpen = vi.fn(),
    user = userEvent.setup()
  const cases = [
    item('demo', 'Fictional VPN acceptance'),
    ...Array.from({ length: 13 }, (_, i) => item(String(i), `Workplace issue ${i}`)),
  ]
  render(<InvestigationHistory cases={cases} busy={false} onOpen={onOpen} allowSamples />)
  expect(screen.queryByRole('button', { name: /Fictional VPN/ })).not.toBeInTheDocument()
  expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await user.click(screen.getByRole('button', { name: /Workplace issue 10/ }))
  expect(onOpen).toHaveBeenCalledWith('10')
  await user.type(screen.getByLabelText('Search investigations'), '12')
  expect(screen.getByRole('button', { name: /Workplace issue 12/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
  await user.clear(screen.getByLabelText('Search investigations'))
  await user.selectOptions(screen.getByLabelText('History'), 'samples')
  expect(screen.getByRole('button', { name: /Fictional VPN/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Workplace issue/ })).not.toBeInTheDocument()
  expect(cases).toHaveLength(14)
})

test('assessment readiness does not mean resolution, and explicit user provenance wins over text labels', () => {
  expect(caseStatus(item('a', 'VPN', { status: 'assessment_ready' }))).toBe('active')
  expect(caseStatus(item('b', 'VPN', { status: 'error' }))).toBe('failed')
  expect(caseStatus(item('c', 'VPN', { resolution_status: 'user_confirmed_resolved' }))).toBe(
    'resolved',
  )
  expect(sampleCase(item('d', 'Fictional issue discussed by a customer', { origin: 'user' }))).toBe(
    false,
  )
})

test('normal workspace never offers sample histories or renders preserved fixture records', async () => {
  const cases = [
    item('sample', 'Fictional VPN acceptance', { origin: 'test' }),
    item('user', 'Real printer issue', { origin: 'user' }),
  ]
  render(<InvestigationHistory cases={cases} busy={false} onOpen={vi.fn()} />)
  expect(screen.queryByLabelText('History')).not.toBeInTheDocument()
  expect(screen.queryByText(/Demo and test/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Fictional VPN/ })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Real printer issue/ })).toBeInTheDocument()
  expect(cases).toHaveLength(2)
})

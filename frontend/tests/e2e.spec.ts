import { test, expect } from '@playwright/test'
import { resumePDF } from './pdf'
const ticket = 'Jake Peralta from B99 cannot connect to the VPN. Authentication failed.'

test('Jake investigation interrupts twice, resumes one case and remembers facts across cases', async ({
  page,
  request,
}) => {
  await page.goto('/')
  await page.getByLabel('Question or ticket').fill(ticket)
  await page.getByRole('button', { name: 'Run DeskMate' }).click()
  await expect(
    page
      .locator('.human-input .question')
      .filter({ hasText: 'Which site or floor is Jake Peralta currently working from?' }),
  ).toBeVisible()
  const caseID = (await page.getByTestId('case-id').textContent())!
  await expect(page.getByText('Ticket analyzed', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Previous incidents searched: 1 result(s)', { exact: true }),
  ).toBeVisible()
  const first = await request.get(`/cases/${caseID}`)
  expect((await first.json()).status).toBe('needs_input')
  await page.getByLabel('Your answer').fill('2nd floor')
  await page.getByRole('button', { name: 'Continue Investigation' }).click()
  await expect(
    page
      .locator('.human-input .question')
      .filter({ hasText: 'Has Jake Peralta installed the required VPN client update?' }),
  ).toBeVisible()
  await expect(page.getByTestId('case-id')).toHaveText(caseID)
  await page.getByLabel('Your answer').fill('Yes')
  await page.getByRole('button', { name: 'Continue Investigation' }).click()
  await expect(page.locator('.status-pill')).toHaveText('assessment ready')
  await expect(
    page.getByText(/The required update is confirmed installed. Refresh the client profile/),
  ).toBeVisible()
  await expect(page.getByText('B99 2nd floor', { exact: true })).toBeVisible()
  await expect(page.getByText('Yes', { exact: true })).toBeVisible()
  const final = await (await request.get(`/cases/${caseID}`)).json()
  expect(final.thread_id).toBe(caseID)
  expect(final.confirmed_facts).toHaveLength(2)
  for (const tool of ['get_active_alerts', 'get_recent_changes', 'search_previous_tickets']) {
    expect(
      final.evidence.some((e: any) => e.tool === tool && e.content.site === 'B99 2nd floor'),
    ).toBeTruthy()
  }
  const events = await (await request.get(`/cases/${caseID}/events`)).json()
  expect(events.filter((e: any) => e.tool === 'analyze_ticket')).toHaveLength(1)
  expect(events.filter((e: any) => e.tool === 'remember_fact')).toHaveLength(3)
  await page.screenshot({ path: 'test-results/jake-assessment.png', fullPage: true })
  await page.getByLabel('Question or ticket').fill(ticket)
  await page.getByRole('button', { name: 'Run DeskMate' }).click()
  await expect(page.getByTestId('case-id')).not.toHaveText(caseID)
  await expect(
    page.getByRole('heading', { name: 'Remembered facts from previous cases' }),
  ).toBeVisible()
  await expect(page.locator('.status-pill')).toHaveText('assessment ready')
  const secondID = (await page.getByTestId('case-id').textContent())!
  const second = await (await request.get(`/cases/${secondID}`)).json()
  expect(second.remembered_facts).toHaveLength(3)
  expect(second.remembered_facts.every((f: any) => f.case_id === caseID)).toBeTruthy()
  await expect(page.getByLabel('Your answer')).toHaveCount(0)
})

// Real browser requests, real MCP subprocess and temporary persistent stores.
test('resume question uses knowledge and never asks for a floor', async ({ page, request }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'PDF', exact: true }).click()
  await page.getByLabel('Document title').fill('Abdul resume')
  await page
    .getByLabel('PDF document')
    .setInputFiles({ name: 'abdul-resume.pdf', mimeType: 'application/pdf', buffer: resumePDF() })
  await page.getByRole('button', { name: 'Add to knowledge', exact: true }).click()
  await expect(page.getByText(/knowledge chunks ingested/)).toBeVisible()
  const before = (await (await request.get('/cases')).json()).length
  const answerResponse = page.waitForResponse(
    (r) => r.url().endsWith('/ask') && r.request().method() === 'POST',
  )
  await page.getByLabel('Question or ticket').fill('What year did Abdul graduate?')
  await page.getByRole('button', { name: 'Run DeskMate', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Answer', exact: true })).toBeVisible()
  await expect(page.getByText('Abdul graduated in 2022.', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Your answer')).toHaveCount(0)
  const response = await (await answerResponse).json()
  expect(response.case_id).toBeNull()
  expect(response.intent).toBe('knowledge_question')
  expect(response.result.workflow).toBe('knowledge')
  expect((await (await request.get('/cases')).json()).length).toBe(before)
  const events = response.events
  expect(events.some((e: any) => e.tool === 'verify')).toBeTruthy()
  expect(events.some((e: any) => e.tool === 'get_site_context')).toBeFalsy()
  await page.getByText('Agent activity', { exact: true }).click()
  await expect(page.getByText('search_knowledge', { exact: true })).toBeVisible()
})

for (const [title, ticket, tool] of [
  ['MFA', 'Sarah cannot complete Microsoft 365 MFA.', 'get_user_context'],
  ['printer', 'Accounting cannot print invoices to PRN-ACCT-02.', 'get_device_context'],
]) {
  test(`${title} investigation chooses relevant tools without a floor question`, async ({
    page,
    request,
  }) => {
    await page.goto('/')
    await page.getByLabel('Question or ticket').fill(ticket)
    await page.getByRole('button', { name: 'Run DeskMate', exact: true }).click()
    await expect(page.locator('.status-pill')).toHaveText('assessment ready')
    await expect(page.getByLabel('Your answer')).toHaveCount(0)
    const cid = (await page.getByTestId('case-id').textContent())!
    const state = await (await request.get(`/cases/${cid}`)).json()
    expect(state.workflow).toBe('service_desk')
    const events = await (await request.get(`/cases/${cid}/events`)).json()
    expect(events.some((e: any) => e.tool === tool)).toBeTruthy()
    expect(events.some((e: any) => e.tool === 'verify')).toBeTruthy()
    expect(events.some((e: any) => e.tool === 'get_site_context')).toBeFalsy()
    await expect(page.getByRole('heading', { name: 'Contradictions', exact: true })).toBeVisible()
  })
}

test('empty proxy response shows a readable error and a retry reaches the real agent', async ({
  page,
  request,
}) => {
  let failed = false
  await page.route('**/ask', async (route) => {
    if (!failed) {
      failed = true
      await route.fulfill({ status: 502, body: '', contentType: 'text/plain' })
    } else await route.continue()
  })
  const before = (await (await request.get('/cases')).json()).length
  await page.goto('/')
  await page.getByLabel('Question or ticket').fill('Sarah cannot complete Microsoft 365 MFA.')
  await page.getByRole('button', { name: 'Run DeskMate', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText(
    'The DeskMate server is temporarily unavailable (HTTP 502). Please try again.',
  )
  await expect(page.getByRole('button', { name: 'Run DeskMate', exact: true })).toBeEnabled()
  expect((await (await request.get('/cases')).json()).length).toBe(before)
  await page.getByRole('button', { name: 'Run DeskMate', exact: true }).click()
  await expect(page.locator('.status-pill')).toHaveText('assessment ready')
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect((await (await request.get('/cases')).json()).length).toBe(before + 1)
  const caseID = (await page.getByTestId('case-id').textContent())!
  const events = await (await request.get(`/cases/${caseID}/events`)).json()
  expect(events.some((e: any) => e.tool === 'get_user_context')).toBeTruthy()
  expect(events.some((e: any) => e.tool === 'verify')).toBeTruthy()
})

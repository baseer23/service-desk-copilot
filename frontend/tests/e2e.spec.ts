import { test, expect } from '@playwright/test'
const ticket = 'Jake Peralta from B99 cannot connect to the VPN. Authentication failed.'

test('Jake investigation interrupts twice, resumes one case and remembers facts across cases', async ({
  page,
  request,
}) => {
  await page.goto('/')
  await page.getByLabel('Ticket details').fill(ticket)
  await page.getByRole('button', { name: 'Start Investigation' }).click()
  await expect(
    page.getByText('Which site or floor is Jake Peralta currently working from?'),
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
    page.getByText('Has Jake Peralta installed the required VPN client update?'),
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
  expect(events.filter((e: any) => e.tool === 'remember_fact')).toHaveLength(2)
  await page.screenshot({ path: 'test-results/jake-assessment.png', fullPage: true })
  await page.getByLabel('Ticket details').fill(ticket)
  await page.getByRole('button', { name: 'Start Investigation' }).click()
  await expect(page.getByTestId('case-id')).not.toHaveText(caseID)
  await expect(
    page.getByRole('heading', { name: 'Remembered facts from previous cases' }),
  ).toBeVisible()
  await expect(page.locator('.status-pill')).toHaveText('assessment ready')
  const secondID = (await page.getByTestId('case-id').textContent())!
  const second = await (await request.get(`/cases/${secondID}`)).json()
  expect(second.remembered_facts).toHaveLength(2)
  expect(second.remembered_facts.every((f: any) => f.case_id === caseID)).toBeTruthy()
  await expect(page.getByLabel('Your answer')).toHaveCount(0)
})

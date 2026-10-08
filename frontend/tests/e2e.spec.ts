import { test, expect } from '@playwright/test';

const API_BASE = 'http://127.0.0.1:8000';

async function apiPost(path: string, body: any) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function apiGet(path: string) {
  const res = await fetch(`${API_BASE}${path}`);
  return res.json();
}

test('B99 Jake investigation end-to-end', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  // 1. open frontend
  await page.goto('/');
  await expect(page.locator('h1')).toHaveText('DeskMate');

  // 2. create case via browser fetch
  const caseId = 'b99-jake-e2e-1';
  const ticket = {
    summary: 'Jake Peralta from B99 cannot connect to the VPN. Authentication failed.',
    user: 'Jake Peralta',
    company: 'B99',
  };

  const create = await page.evaluate(async (caseId, ticket) => {
    const res = await fetch('http://127.0.0.1:8000/cases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: caseId, state: { ticket } }),
    });
    return res.json();
  }, caseId, ticket);
  expect(create.case_id).toBe(caseId);

  // 3. invoke investigation
  const investigate = await page.evaluate(async (caseId, ticket) => {
    const res = await fetch(`http://127.0.0.1:8000/cases/${caseId}/investigate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket }),
    });
    return res.json();
  }, caseId, ticket);

  expect(investigate.case_id).toBe(caseId);
  const result = investigate.result;
  expect(result).toBeDefined();
  // expect the agent to ask for missing info (site or device)
  expect(result.pending_question || result.missing_information).toBeTruthy();

  // 4. supply human answer for site/floor
  const resume1 = await page.evaluate(async (caseId) => {
    const res = await fetch(`http://127.0.0.1:8000/cases/${caseId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: '2nd floor' }),
    });
    return res.json();
  }, caseId);
  expect(resume1.case_id).toBe(caseId);

  // If the agent asks another human question, respond accordingly
  if (resume1.result && resume1.result.pending_question && resume1.result.pending_question.toLowerCase().includes('watchguard')) {
    const resume2 = await page.evaluate(async (caseId) => {
      const res = await fetch(`http://127.0.0.1:8000/cases/${caseId}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: 'Yes' }),
      });
      return res.json();
    }, caseId);
    expect(resume2.case_id).toBe(caseId);
  }

  // Finally, fetch the case and ensure final_answer exists or status resolved
  const final = await page.evaluate(async (caseId) => {
    const res = await fetch(`http://127.0.0.1:8000/cases/${caseId}`);
    return res.json();
  }, caseId);
  expect(final.case_id).toBe(caseId);
  expect(final.state).toBeDefined();
  expect(final.state.final_answer || final.state.status).toBeTruthy();

  // 5. start a second case and verify remembered fact from first case is retrievable
  const case2 = 'b99-jake-e2e-2';
  const ticket2 = { summary: 'New Jake VPN issue', user: 'Jake Peralta', company: 'B99' };
  await page.evaluate(async (caseId, ticket) => {
    await fetch('http://127.0.0.1:8000/cases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: caseId, state: { ticket } }),
    });
  }, case2, ticket2);

  const inv2 = await page.evaluate(async (caseId, ticket) => {
    const res = await fetch(`http://127.0.0.1:8000/cases/${caseId}/investigate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket }),
    });
    return res.json();
  }, case2, ticket2);

  expect(inv2.case_id).toBe(case2);

  // check memory: query remembered facts for first case via GET /cases/{case_id}
  const mem1 = await page.evaluate(async (caseId) => {
    const res = await fetch(`http://127.0.0.1:8000/cases/${caseId}`);
    return res.json();
  }, caseId);
  expect(mem1.state.remembered_facts).toBeInstanceOf(Array);
  // ensure the remembered '2nd floor' answer appears
  const hasFloor = mem1.state.remembered_facts.some((f: any) => String(f.value).toLowerCase().includes('2nd'));
  expect(hasFloor).toBeTruthy();

  // verify no page console errors
  expect(consoleErrors.length).toBe(0);
});

import { test, expect, type Response } from '@playwright/test'

// Chromium does not retain SSE bodies for Network.getResponseBody. Clone the
// browser's original fetch response for assertions; never send a second POST.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window)
    const responses = new Map<string, Promise<unknown>>()
    Object.assign(window, { workflowTestResponses: responses })
    window.fetch = async (...args) => {
      const response = await nativeFetch(...args)
      if (response.headers.get('content-type')?.includes('text/event-stream')) {
        const parsed = response.clone().text().then(text => {
          for (const block of text.replace(/\r\n/g, '\n').split('\n\n')) {
            if (!block.split('\n').some(line => /^event: ?result$/.test(line))) continue
            return JSON.parse(block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n'))
          }
          throw new Error('The streamed investigation had no result')
        })
        responses.set(response.url, parsed)
      }
      return response
    }
  })
})

async function readWorkflowResponse(response: Response) {
  if (!response.headers()['content-type']?.includes('text/event-stream')) return response.json()
  return response.frame().evaluate(url => Reflect.get(window, 'workflowTestResponses').get(url), response.url())
}
import { resumePDF } from './pdf'
const ticket = 'Jake Peralta from B99 cannot connect to the VPN. Authentication failed.'

for (const summary of [
  'Fictional test: Microsoft 365 MFA fails for Amy Santiago from B99.',
  'Fictional test: VPN authentication fails for Charles Boyle from B99.',
  'Fictional test: printer installation fails for Rosa Diaz from B99.',
  'Fictional test: Outlook email synchronization fails for Terry Jeffords from B99.',
  'Fictional test: Noah Fictional from B99 has lost network connectivity.',
  'Fictional test: Emma Fictional from B99 reports a Microsoft 365 account lockout.',
  'Fictional test: Oliver Fictional from B99 cannot establish a remote desktop connection.',
]) {
  test(`simulated reasoning through real browser and MCP: ${summary}`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.name))
    await page.goto('/')
    await page.getByLabel('Question or ticket').fill(summary)
    await page.getByRole('button', { name: 'Send' }).click()
    await expect(page.locator('.case-heading .status-pill')).toHaveText(/needs input|assessment ready|insufficient information/)
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByText('ISOLATED DEMO', { exact: true }).first()).toBeVisible()
    expect(errors).toEqual([])
  })
}

test('local PDF extraction, staging, dedup and cited keyword search use real HTTP/MCP', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.name))
  await page.goto('/')
  await page.getByRole('button', { name: 'Knowledge', exact: true }).click()
  await page.getByRole('button', { name: 'PDF', exact: true }).click()
  await page.getByLabel('Document title').fill('Fictional local PDF')
  await page.getByLabel('PDF document').setInputFiles({ name: 'fictional.pdf', mimeType: 'application/pdf', buffer: resumePDF() })
  await page.getByRole('button', { name: 'Extract text', exact: true }).click()
  await expect(page.locator('.document-preview')).toContainText('2022')
  await expect(page.getByText(/Text preview is ready/)).toBeVisible()
  await page.getByRole('button', { name: 'Save document' }).click()
  await expect(page.getByText(/Your document is saved/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Saved documents' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Save document' }).click()
  await expect(page.getByText('This document is already saved.')).toBeVisible()
  await page.getByRole('textbox', { name: 'Knowledge search', exact: true }).fill('graduated')
  await page.getByRole('button', { name: 'Search knowledge' }).click()
  await expect(page.getByLabel('Knowledge search results')).toContainText('2022')
  await expect(page.getByLabel('Knowledge search results')).toContainText('Source:')
  expect(errors).toEqual([])
})

test('save and reopen an uninvestigated local case then explicitly investigate', async ({ page }) => {
  await page.goto('/')
  const summary = 'Fictional printer offline request for local saving'
  await page.getByLabel('Question or ticket').fill(summary)
  await page.getByRole('button', { name: 'Save request' }).click()
  await expect(page.locator('.case-heading .status-pill')).toHaveText('new')
  await expect(page.getByText('Your request is saved. Automated investigation has not started.')).toBeVisible()
  const id = await page.getByTestId('case-id').textContent()
  await page.reload()
  await page.getByRole('button', { name: 'Investigations', exact: true }).click()
  await page.getByLabel('History', {exact:true}).selectOption('samples')
  await page.getByRole('button', { name: `${summary} new`, exact: true }).click()
  await expect(page.getByTestId('case-id')).toHaveText(id!)
  await page.getByRole('button', { name: 'Investigate saved case' }).click()
  await expect(page.locator('.case-heading .status-pill')).not.toHaveText('new')
})

test('Jake investigation interrupts twice, resumes one case and remembers facts across cases', async ({
  page,
  request,
}) => {
  await page.goto('/')
  await page.getByLabel('Question or ticket').fill(ticket)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(
    page
      .locator('.human-input .question')
      .filter({ hasText: 'Which site or floor is Jake Peralta currently working from?' }),
  ).toBeVisible()
  const caseID = (await page.getByTestId('case-id').textContent())!
  await page.getByText('Investigation progress',{exact:true}).click()
  await expect(page.getByText('Ticket analyzed', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Checking previous incidents', { exact: true }),
  ).toBeVisible()
  const first = await request.get(`/cases/${caseID}`)
  expect((await readWorkflowResponse(first)).status).toBe('needs_input')
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
  await expect(page.locator('.case-heading .status-pill')).toHaveText('assessment ready')
  await page.getByText('Investigation details',{exact:true}).click()
  await expect(
    page.locator('dd.next-action').getByText(
      /The required update is confirmed installed. Refresh the client profile/,
    ),
  ).toBeVisible()
  await expect(page.locator('.assessment strong').filter({ hasText: /^B99 2nd floor$/ })).toBeVisible()
  await expect(page.locator('.assessment strong').filter({ hasText: /^Yes$/ })).toBeVisible()
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
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByTestId('case-id')).not.toHaveText(caseID)
  if (!await page.locator('details.assessment').evaluate(element => element.hasAttribute('open'))) await page.getByText('Investigation details',{exact:true}).click()
  await expect(
    page.getByRole('heading', { name: 'Remembered facts from previous cases' }),
  ).toBeVisible()
  await expect(page.locator('.case-heading .status-pill')).toHaveText('assessment ready')
  const secondID = (await page.getByTestId('case-id').textContent())!
  const second = await (await request.get(`/cases/${secondID}`)).json()
  expect(second.remembered_facts).toHaveLength(3)
  expect(second.remembered_facts.every((f: any) => f.case_id === caseID)).toBeTruthy()
  await expect(page.getByLabel('Your answer')).toHaveCount(0)
})

// Real browser requests, real MCP subprocess and temporary persistent stores.
test('resume question uses knowledge and never asks for a floor', async ({ page, request }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Knowledge', exact: true }).click()
  await page.getByRole('button', { name: 'PDF', exact: true }).click()
  await page.getByLabel('Document title').fill('Abdul resume')
  await page
    .getByLabel('PDF document')
    .setInputFiles({ name: 'abdul-resume.pdf', mimeType: 'application/pdf', buffer: resumePDF() })
  await page.getByRole('button', { name: 'Add to knowledge', exact: true }).click()
  await expect(page.getByText(/Your document was added to knowledge/)).toBeVisible()
  const before = (await (await request.get('/cases')).json()).length
  const answerResponse = page.waitForResponse(
    (r) => r.url().endsWith('/ask') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.getByLabel('Question or ticket').fill('What year did Abdul graduate?')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Final Answer', exact: true })).toBeVisible()
  await expect(page.getByText('Abdul graduated in 2022.', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Your answer')).toHaveCount(0)
  const response = await readWorkflowResponse(await answerResponse)
  expect(response.case_id).toBeNull()
  expect(response.intent).toBe('knowledge_question')
  expect(response.result.workflow).toBe('knowledge')
  expect((await (await request.get('/cases')).json()).length).toBe(before)
  const events = response.events
  expect(events.some((e: any) => e.tool === 'verify')).toBeTruthy()
  expect(events.some((e: any) => e.tool === 'get_site_context')).toBeFalsy()
  await page.getByText('Investigation progress', { exact: true }).click()
  await expect(page.getByTestId('investigation-scroll').first()).toBeVisible()
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
    await page.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(page.locator('.case-heading .status-pill')).toHaveText('assessment ready')
    await expect(page.getByLabel('Your answer')).toHaveCount(0)
    const cid = (await page.getByTestId('case-id').textContent())!
    const state = await (await request.get(`/cases/${cid}`)).json()
    expect(state.workflow).toBe('service_desk')
    const events = await (await request.get(`/cases/${cid}/events`)).json()
    expect(events.some((e: any) => e.tool === tool)).toBeTruthy()
    expect(events.some((e: any) => e.tool === 'verify')).toBeTruthy()
    expect(events.some((e: any) => e.tool === 'get_site_context')).toBeFalsy()
    await page.getByText('Investigation details',{exact:true}).click()
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
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText(
    'The DeskMate server is temporarily unavailable (HTTP 502). Please try again.',
  )
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled()
  expect((await (await request.get('/cases')).json()).length).toBe(before)
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.locator('.case-heading .status-pill')).toHaveText('assessment ready')
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect((await (await request.get('/cases')).json()).length).toBe(before + 1)
  const caseID = (await page.getByTestId('case-id').textContent())!
  const events = await (await request.get(`/cases/${caseID}/events`)).json()
  expect(events.some((e: any) => e.tool === 'get_user_context')).toBeTruthy()
  expect(events.some((e: any) => e.tool === 'verify')).toBeTruthy()
})

test('unconfigured Speech is unavailable while typed helpdesk chat remains usable', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Voice', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Ready to Talk', exact: true })).toBeDisabled()
  await expect(page.getByText('Speech recognition is unavailable. You can still type your request below.')).toBeVisible()
  await page.getByLabel('Message DeskMate').fill('Sarah cannot complete Microsoft 365 MFA.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page.getByTestId('voice-message')).toHaveCount(3)
  await expect(
    page.getByTestId('voice-transcript').getByText('Sarah cannot complete Microsoft 365 MFA.'),
  ).toBeVisible()
})

test('isolated microphone permission rejection opens no Speech connection and preserves typed input', async ({ page }) => {
  await page.route('**/speech/readiness', route => route.fulfill({ json: { ready: true } }))
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: async () => { throw new DOMException('Isolated permission rejection', 'NotAllowedError') },
      enumerateDevices: async () => [],
    } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Voice', exact: true }).click()
  await page.getByRole('button', { name: 'Ready to Talk', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Microphone permission was denied')
  await expect(page.getByLabel('Message DeskMate')).toBeEnabled()
})

test('isolated Speech transport verifies continuous partials, reconnect, Stop exclusion and microphone cleanup', async ({ page, request }) => {
  // Explicit browser-only stand-ins: this does not claim real Azure recognition.
  const processor = await request.get('/pcm-worklet.js')
  expect(processor.status()).toBe(200)
  await page.route('**/speech/readiness', route => route.fulfill({ json: { ready: true } }))
  await page.route('**/voice-conversation/start', route => route.fulfill({ json: {
    session_id: 'voice-browser-test',
    response: 'Hello, welcome to Digital nGenuity support. How can I help you today?',
  } }))
  let submissions = 0
  await page.route('**/voice-conversation/voice-browser-test', async route => {
    submissions++
    await route.fulfill({ json: { response: 'Let us troubleshoot that application issue.' } })
  })
  await page.addInitScript(() => {
    const stats = { frames: 0, tracksStopped: 0, socketsClosed: 0 }
    Object.assign(window, { speechTestStats: stats })
    const node = () => ({ connect() {}, disconnect() {} })
    const track = { enabled: true, readyState: 'live', stop() { stats.tracksStopped++ } }
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }),
      enumerateDevices: async () => [],
    } })
    class Context {
      sampleRate = 16000; state = 'running'; destination = {}
      audioWorklet = { addModule: async (url: string) => { const response = await fetch(url); if (!response.ok) throw new Error('processor unavailable') } }
      createMediaStreamSource() { return node() }
      createGain() { return { ...node(), gain: { value: 1 } } }
      async resume() {} async close() {}
    }
    class Worklet {
      port: { onmessage: ((event: { data: { audio: ArrayBuffer; level: number } }) => void) | null } = { onmessage: null }
      timer = setInterval(() => this.port.onmessage?.({ data: { audio: new ArrayBuffer(640), level: 0.1 } }), 20)
      connect() {} disconnect() { clearInterval(this.timer) }
    }
    class SpeechSocket {
      static OPEN = 1
      readyState = 1; bufferedAmount = 0; stopped = false; closed = false
      onmessage: ((event: { data: string }) => void) | null = null
      onclose: ((event: { code: number }) => void) | null = null
      constructor() {
        Object.assign(window, { speechTestEmit: (type: string, value: object) => this.emit(type, value) })
        setTimeout(() => this.emit('state', { state: 'listening' }), 30)
      }
      emit(type: string, value: object) { this.onmessage?.({ data: JSON.stringify({ type, ...value }) }) }
      send(data: string | ArrayBuffer) {
        if (typeof data !== 'string') { if (data.byteLength === 640) stats.frames++; return }
        if (JSON.parse(data).type === 'stop' && !this.stopped) {
          this.stopped = true
          setTimeout(() => { this.emit('state', { state: 'stopped' }); this.close() }, 0)
        }
      }
      close() { if (!this.closed) { this.closed = true; stats.socketsClosed++; this.onclose?.({ code: 1000 }) } }
    }
    Object.assign(window, { AudioContext: Context, AudioWorkletNode: Worklet, WebSocket: SpeechSocket })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Voice', exact: true }).click()
  await page.getByRole('button', { name: 'Ready to Talk', exact: true }).click()
  await expect(page.getByTestId('voice-status')).toHaveText('Listening')
  await expect.poll(() => page.evaluate(() => Reflect.get(window, 'speechTestStats').frames)).toBeGreaterThan(0)
  await page.evaluate(() => {
    const emit = Reflect.get(window, 'speechTestEmit')
    emit('transcript.partial', { id: '1:a', text: 'VPN auth' })
    emit('transcript.partial', { id: '1:a', text: 'VPN authentication fails' })
  })
  await expect(page.getByTestId('voice-message')).toHaveCount(2)
  await page.evaluate(() => {
    const emit = Reflect.get(window, 'speechTestEmit')
    emit('transcript.final', { id: '1:a', text: 'VPN authentication fails.' })
    emit('transcript.final', { id: '1:a', text: 'VPN authentication fails.' })
    emit('state', { state: 'reconnecting' })
  })
  await expect(page.getByTestId('voice-status')).toHaveText('Reconnecting')
  await page.evaluate(() => {
    const emit = Reflect.get(window, 'speechTestEmit')
    emit('state', { state: 'listening' })
    emit('transcript.final', { id: '2:b', text: 'The application will stop responding.' })
    emit('usage', { connected_seconds: 45 })
  })
  await expect(page.getByTestId('voice-message')).toHaveCount(1)
  await expect(page.getByLabel('Speech usage')).toContainText('Connected speech 45s')
  await page.getByRole('button', { name: 'Mute', exact: true }).click()
  await expect(page.getByTestId('voice-status')).toHaveText('Microphone muted')
  await page.getByRole('button', { name: 'Unmute', exact: true }).click()
  await page.evaluate(() => Reflect.get(window, 'speechTestEmit')('transcript.final', { id: '2:stop', text: 'Stop.' }))
  await expect(page.getByTestId('voice-status')).toHaveText('Ready')
  await expect(page.getByText('VPN authentication fails. The application will stop responding.')).toBeVisible()
  await expect(page.getByText('Let us troubleshoot that application issue.')).toBeVisible()
  expect(submissions).toBe(1)
  await expect.poll(() => page.evaluate(() => Reflect.get(window, 'speechTestStats').tracksStopped)).toBe(1)
  await page.getByRole('button', { name: 'Ready to Talk', exact: true }).click()
  await expect(page.getByTestId('voice-status')).toHaveText('Listening')
  await page.getByRole('button', { name: 'Stop session', exact: true }).click()
  await expect.poll(() => page.evaluate(() => Reflect.get(window, 'speechTestStats').tracksStopped)).toBe(2)
})

test('investigation timeline and huge sources scroll independently with intentional full expansion', async ({ page }) => {
  const content='Large vendor evidence '.repeat(15000)+'FULL_EVIDENCE_END'
  const current={case_id:'scroll-case',thread_id:'scroll-case',ticket:{summary:'Fictional VPN investigation'},status:'assessment_ready',workflow:'service_desk',confidence:0.5,pending_question:null,likely_issue:null,hypotheses:[],confirmed_facts:[],remembered_facts:[],inferences:[],uncertainty:['Resolution is unverified.'],contradictions:[],recommended_next_action:null,final_answer:null,evidence:Array.from({length:20},(_,i)=>({tool:'fetch_web_page',source:'Vendor reference '+i,source_system:'public_web',source_quality:'primary',url:'https://learn.microsoft.com/reference/'+i,observed_at:'2026-10-12T00:00:00Z',content}))}
  const events=Array.from({length:1000},(_,id)=>({id,tool:'search_knowledge',source:'stub',content:'Private draft',created_at:'2026-10-12T00:00:00Z'}))
  await page.route('**/cases',route=>route.fulfill({json:[current]}))
  await page.route('**/cases/scroll-case',route=>route.fulfill({json:current}))
  await page.route('**/cases/scroll-case/events',route=>route.fulfill({json:events}))
  await page.goto('/')
  await page.getByRole('button', { name: 'Investigations', exact: true }).click()
  await page.getByLabel('History', {exact:true}).selectOption('samples')
  await page.getByRole('button',{name:/Fictional VPN investigation/}).click()
  await page.getByText('Investigation progress',{exact:true}).click()
  const timeline=page.getByTestId('investigation-scroll').first()
  const sources=page.getByTestId('evidence-scroll')
  await expect(timeline.locator('li')).toHaveCount(1000)
  for(const panel of [timeline,sources]) {
    const geometry=await panel.evaluate(el=>({height:el.clientHeight,scroll:el.scrollHeight,overflow:getComputedStyle(el).overflowY}))
    expect(geometry.height).toBeLessThanOrEqual(520)
    expect(geometry.scroll).toBeGreaterThan(geometry.height)
    expect(geometry.overflow).toBe('auto')
  }
  await expect(page.getByText('Private draft')).toHaveCount(0)
  await expect(page.getByText(/FULL_EVIDENCE_END/)).toHaveCount(0)
  await sources.getByText('Show full evidence',{exact:true}).first().click()
  await expect(sources.getByLabel('Full evidence')).toContainText('FULL_EVIDENCE_END')
  expect(await page.evaluate(()=>document.body.scrollHeight)).toBeLessThan(5500)
  await page.screenshot({path:'test-results/investigation-bounded-panels.png',fullPage:true})
})

test('unrelated general questions stop before investigation or research', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Question or ticket').fill('What is the latest on immigration in Canada?')
  const response=page.waitForResponse(r=>r.url().endsWith('/ask')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'Send',exact:true}).click()
  const body=await readWorkflowResponse(await response)
  expect(body.case_id).toBeNull();expect(body.result.evidence).toHaveLength(0)
  expect(body.events.every((event:{tool:string})=>event.tool==='classify_intent')).toBeTruthy()
  await expect(page.getByText('DeskMate is configured for IT support and internal knowledge investigations. Please ask a service-desk or uploaded-document question.')).toBeVisible()
})

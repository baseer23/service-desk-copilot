import { test, expect } from '@playwright/test'
import { resumePDF } from './pdf'
const ticket = 'Jake Peralta from B99 cannot connect to the VPN. Authentication failed.'

for (const summary of [
  'Fictional test: Microsoft 365 MFA fails for Amy Santiago from B99.',
  'Fictional test: VPN authentication fails for Charles Boyle from B99.',
  'Fictional test: the printer is offline for Rosa Diaz from B99.',
  'Fictional test: Outlook is not opening for Terry Jeffords from B99.',
]) {
  test(`simulated reasoning through real browser and MCP: ${summary}`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.name))
    await page.goto('/')
    await page.getByLabel('Question or ticket').fill(summary)
    await page.getByRole('button', { name: 'Run DeskMate' }).click()
    await expect(page.locator('.status-pill')).toHaveText(/needs input|assessment ready|insufficient information/)
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByText('ISOLATED DEMO', { exact: true }).first()).toBeVisible()
    expect(errors).toEqual([])
  })
}

test('local PDF extraction, staging, dedup and cited keyword search use real HTTP/MCP', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.name))
  await page.goto('/')
  await page.getByRole('button', { name: 'PDF', exact: true }).click()
  await page.getByLabel('Document title').fill('Fictional local PDF')
  await page.getByLabel('PDF document').setInputFiles({ name: 'fictional.pdf', mimeType: 'application/pdf', buffer: resumePDF() })
  await page.getByRole('button', { name: 'Extract text', exact: true }).click()
  await expect(page.locator('.document-preview')).toContainText('2022')
  await expect(page.getByText(/No Azure request was made/)).toBeVisible()
  await page.getByRole('button', { name: 'Save document locally' }).click()
  await expect(page.getByText(/chunks saved locally/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Locally saved documents' })).toBeVisible()
  await page.getByRole('button', { name: 'Save document locally' }).click()
  await expect(page.getByText('This document is already saved locally.')).toBeVisible()
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
  await page.getByRole('button', { name: 'Save request locally' }).click()
  await expect(page.locator('.status-pill')).toHaveText('new')
  await expect(page.getByText('Saved locally. Automated investigation has not started.')).toBeVisible()
  const id = await page.getByTestId('case-id').textContent()
  await page.reload()
  await page.getByRole('button', { name: `${summary} new`, exact: true }).click()
  await expect(page.getByTestId('case-id')).toHaveText(id!)
  await page.getByRole('button', { name: 'Investigate saved case' }).click()
  await expect(page.locator('.status-pill')).not.toHaveText('new')
})

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
    page.getByText('Checking previous incidents', { exact: true }),
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

test('browser support uses isolated real HTTP/MCP, confirmations and case save', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Start text support' }).click()
  await expect(page.getByTestId('voice-transcript')).toContainText('AI support assistant')
  const message = async (text: string, expected: string) => {
    await page.getByLabel('Message DeskMate').fill(text)
    await page.getByRole('button', { name: 'Send message' }).click()
    await expect(page.getByTestId('voice-transcript').locator('article').last()).toContainText(expected)
    await expect(page.getByTestId('voice-status')).not.toHaveText('assistant speaking')
  }
  await message('My name is Alex Test', 'Is that correct?')
  await message('yes', 'email address')
  await message('Email is alex at example dot com', 'Is that correct?')
  await message('yes', 'callback number')
  await message('Callback: +12025550123', 'Is that correct?')
  await message('yes', 'What problem')
  await message('My VPN is broken', 'Did that work?')
  await message('it worked', 'You confirmed')
  await page.getByRole('button', { name: 'Save case summary' }).click()
  await expect(page.getByTestId('case-id')).toContainText('voice-')
  await expect(page.getByTestId('voice-status')).toHaveText('completed')
})

test('permission rejection never opens real microphone and offers text fallback', async ({ page }) => {
  await page.addInitScript(() => { Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: async () => { throw new Error('Synthetic permission rejection') } } }) })
  await page.goto('/')
  await page.getByRole('button', { name: 'Test microphone' }).click()
  await expect(page.getByRole('alert')).toContainText('start text support')
  await page.getByRole('button', { name: 'Start text support' }).click()
  await expect(page.getByTestId('voice-transcript')).toContainText('AI support assistant')
  await page.getByRole('button', { name: 'Stop session' }).click()
})

test('1000 alternating messages keep the browser layout bounded and support jump to latest', async ({ page }) => {
  // Only this stress test synthesizes transcript transport; normal flows use real HTTP.
  await page.addInitScript(() => {
    const Native = window.WebSocket
    class StressSocket extends Native {
      set onmessage(handler: ((this: WebSocket, event: MessageEvent) => void) | null) {
        super.onmessage = event => {
          const value = JSON.parse(event.data)
          if (value.type === 'session.snapshot') {
            if (value.sequence === 1) value.payload.conversation.turns = Array.from({ length: 1000 }, (_, index) => ({ id: 'stress-' + index, role: index % 2 ? 'assistant' : 'user', text: 'Stress message ' + index, state: 'final' }))
            else { value.type = 'status'; value.payload = { status: value.payload.conversation.status } }
          }
          handler?.call(this, new MessageEvent('message', { data: JSON.stringify(value) }))
        }
      }
      get onmessage() { return super.onmessage }
    }
    window.WebSocket = StressSocket
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Start text support' }).click()
  await expect.poll(() => page.getByTestId('voice-message').count()).toBeGreaterThanOrEqual(1000)
  const panel = page.getByTestId('voice-transcript')
  const before = await panel.evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight, body: document.body.scrollHeight, overflow: getComputedStyle(el).overflowY }))
  expect(before.height).toBeLessThanOrEqual(480); expect(before.scroll).toBeGreaterThan(before.height * 10); expect(before.overflow).toBe('auto')
  expect(before.body).toBeLessThan(5000)
  await panel.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll', { bubbles: true })) })
  // A real typed turn now exercises the jump behavior on a live server event.
  await page.getByLabel('Message DeskMate').fill('hello')
  await page.getByRole('button', { name: 'Send message' }).click()
  await expect(page.getByRole('button', { name: 'Jump to latest' })).toBeVisible()
  await page.getByRole('button', { name: 'Jump to latest' }).click()
  await expect(page.getByRole('button', { name: 'Jump to latest' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Stop session' }).click()
})

test('fake microphone plumbing shows stages, cookie, PCM frames and cleanup without real media', async ({ page, context, request }) => {
  const processor=await request.get('/pcm-worklet.js')
  expect(processor.status()).toBe(200)
  expect(processor.headers()['content-type']).toMatch(/javascript/)
  await page.addInitScript(() => {
    const stats={audioFrames:0,pings:0,tracksStopped:0}
    Object.assign(window,{voiceTestStats:stats})
    const node=()=>({connect(){},disconnect(){}})
    Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[{stop(){stats.tracksStopped++}}]})}})
    class Context {
      sampleRate=24000;currentTime=0;state='running';destination={}
      audioWorklet={addModule:async(url:string)=>{const response=await fetch(url);if(!response.ok)throw new Error('processor unavailable')}}
      createMediaStreamSource(){return node()}
      createGain(){return {...node(),gain:{value:1}}}
      createBuffer(_channels:number,length:number,rate:number){return {getChannelData:()=>new Float32Array(length),duration:length/rate}}
      createBufferSource(){return {...node(),buffer:null,onended:null,start(){},stop(){}}}
      async resume(){}async close(){}
    }
    class Worklet {
      port:{onmessage:((event:{data:ArrayBuffer})=>void)|null}={onmessage:null}
      timer=setInterval(()=>this.port.onmessage?.({data:new ArrayBuffer(960)}),20)
      connect(){}disconnect(){clearInterval(this.timer)}
    }
    Object.assign(window,{AudioContext:Context,AudioWorkletNode:Worklet})
    const Native=window.WebSocket
    class Observed extends Native {
      send(data:string|ArrayBufferLike|Blob|ArrayBufferView){
        if(typeof data==='string'){const message=JSON.parse(data);if(message.type==='audio.append')stats.audioFrames++;if(message.type==='ping')stats.pings++}
        super.send(data)
      }
    }
    window.WebSocket=Observed
  })
  await page.goto('/')
  const voice=page.getByRole('region',{name:'Voice Support'})
  await expect(voice.getByText('ISOLATED DEMO',{exact:true})).toBeVisible()
  const mic=voice.getByRole('button',{name:'Test microphone',exact:true})
  await expect(mic).toBeEnabled()
  await mic.focus()
  expect(await mic.evaluate(el=>getComputedStyle(el).outlineStyle)).not.toBe('none')
  expect(await mic.evaluate(el=>getComputedStyle(el).borderRadius)).not.toBe('0px')
  await mic.click()
  await expect(page.getByTestId('voice-status')).toHaveText('listening')
  await expect(page.getByRole('list',{name:'Connection progress'})).toContainText('Requesting microphone permission')
  await expect.poll(()=>page.evaluate(()=>Reflect.get(window,'voiceTestStats').audioFrames)).toBeGreaterThan(0)
  expect(await page.evaluate(()=>Reflect.get(window,'voiceTestStats').pings)).toBe(1)
  expect((await context.cookies()).some(cookie=>cookie.name==='deskmate_voice_identity' && cookie.httpOnly)).toBeTruthy()
  const controls=await voice.locator('.voice-controls').boundingBox()
  const activity=await voice.locator('.voice-activity').boundingBox()
  expect(controls!.y).toBeGreaterThanOrEqual(activity!.y+activity!.height)
  await page.screenshot({path:'test-results/voice-support-polished.png',fullPage:true})
  await page.getByRole('button',{name:'Stop session'}).click()
  await expect(page.getByTestId('voice-status')).toHaveText('stopped')
  expect(await page.evaluate(()=>Reflect.get(window,'voiceTestStats').tracksStopped)).toBeGreaterThan(0)
  await expect(page.getByRole('button',{name:'Reconnect with text'})).toHaveCount(0)
})

test('investigation timeline and huge sources scroll independently with intentional full expansion', async ({ page }) => {
  const content='Large vendor evidence '.repeat(15000)+'FULL_EVIDENCE_END'
  const current={case_id:'scroll-case',thread_id:'scroll-case',ticket:{summary:'Fictional VPN investigation'},status:'assessment_ready',workflow:'service_desk',confidence:0.5,pending_question:null,likely_issue:null,hypotheses:[],confirmed_facts:[],remembered_facts:[],inferences:[],uncertainty:['Resolution is unverified.'],contradictions:[],recommended_next_action:null,final_answer:null,evidence:Array.from({length:20},(_,i)=>({tool:'fetch_web_page',source:'Vendor reference '+i,source_system:'public_web',source_quality:'primary',url:'https://learn.microsoft.com/reference/'+i,observed_at:'2026-10-12T00:00:00Z',content}))}
  const events=Array.from({length:1000},(_,id)=>({id,tool:'search_knowledge',source:'stub',content:'Private draft',created_at:'2026-10-12T00:00:00Z'}))
  await page.route('**/cases',route=>route.fulfill({json:[current]}))
  await page.route('**/cases/scroll-case',route=>route.fulfill({json:current}))
  await page.route('**/cases/scroll-case/events',route=>route.fulfill({json:events}))
  await page.goto('/')
  await page.getByRole('button',{name:/Fictional VPN investigation/}).click()
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
  await page.getByRole('button',{name:'Run DeskMate',exact:true}).click()
  const body=await (await response).json()
  expect(body.case_id).toBeNull();expect(body.result.evidence).toHaveLength(0)
  expect(body.events.every((event:{tool:string})=>event.tool==='classify_intent')).toBeTruthy()
  await expect(page.getByText('DeskMate is configured for IT support and internal knowledge investigations. Please ask a service-desk or uploaded-document question.')).toBeVisible()
})

test('a stale isolated-demo cookie permits a fresh text session after restart', async ({ page, context }) => {
  await context.addCookies([{ name: 'deskmate_voice_identity', value: 'stale-demo-cookie', url: 'http://127.0.0.1:5184', httpOnly: true, sameSite: 'Strict' }])
  await page.goto('/')
  await page.getByRole('button', { name: 'Start text support', exact: true }).click()
  await expect(page.getByTestId('voice-status')).toHaveText('listening')
  await expect(page.getByTestId('voice-message')).toContainText('AI support assistant')
  const cookies = await context.cookies()
  expect(cookies.some(cookie => cookie.name === 'deskmate_voice_identity' && cookie.value !== 'stale-demo-cookie' && cookie.httpOnly)).toBeTruthy()
  await page.getByRole('button', { name: 'Stop session' }).click()
})

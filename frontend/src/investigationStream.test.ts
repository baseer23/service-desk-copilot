import { afterEach, expect, test, vi } from 'vitest'
import { streamInvestigation } from './investigationStream'

afterEach(()=>vi.unstubAllGlobals())

test('parses split SSE frames and preserves operational event order without a repeated POST',async()=>{
 const parts=['event: activity\ndata: {"sequence":1,"stage":"plan","status":"running","summary":"Planning"}\n','\nevent: activity\ndata: {"sequence":2,"stage":"plan","status":"completed","summary":"Planning"}\n\nevent: result\ndata: {"answer":"Supported"}\n\n']
 const body=new ReadableStream({start(controller){for(const part of parts)controller.enqueue(new TextEncoder().encode(part));controller.close()}})
 const fetch=vi.fn(async()=>new Response(body,{headers:{'Content-Type':'text/event-stream'}}));vi.stubGlobal('fetch',fetch)
 const events:unknown[]=[]
 expect(await streamInvestigation('/ask',{summary:'Question'},event=>events.push(event))).toEqual({answer:'Supported'})
 expect(events).toHaveLength(2);expect(fetch).toHaveBeenCalledTimes(1)
})

test('incomplete SSE fails explicitly without retrying the graph',async()=>{
 const fetch=vi.fn(async()=>new Response('event: activity\ndata: {"stage":"plan"}\n\n',{headers:{'Content-Type':'text/event-stream'}}));vi.stubGlobal('fetch',fetch)
 await expect(streamInvestigation('/ask',{},()=>undefined)).rejects.toThrow('connection ended')
 expect(fetch).toHaveBeenCalledTimes(1)
})

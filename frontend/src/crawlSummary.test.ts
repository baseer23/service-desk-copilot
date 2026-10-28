import { describe, expect, it } from 'vitest'
import { crawlSummary } from './KnowledgePanel'

describe('crawlSummary', () => {
  it('reports crawl, indexing and linking progress with readable skip reasons', () => {
    const summary = crawlSummary({
      job_id: 'job', url: 'https://site.example/', status: 'complete',
      pages_found: 20, pages_saved: 14, pages_duplicate: 1, pages_skipped: 4,
      skipped_reasons: { robots_disallowed: 1, not_html: 1, rate_limited: 1, http_404: 1 },
      document_ids: Array.from({ length: 15 }, (_, index) => `d${index}`),
      pages_indexed: 10, pages_linked: 8, pages_failed: 0,
    })
    expect(summary.text).toContain('Crawl complete — pages found 20, saved 14 (+1 already saved), skipped 4')
    expect(summary.text).toContain('Indexed 10 of 15 · Neo4j linked 8 of 15')
    expect(summary.text).toContain('1 blocked by robots.txt, 1 not HTML, 1 rate limited, 1 HTTP 404')
    expect(summary.terminal).toBe(false)
  })

  it('stops polling when every saved page is indexed or failed, or the crawl failed', () => {
    const base = { job_id: 'job', url: 'https://site.example/', document_ids: ['a', 'b'] }
    expect(crawlSummary({ ...base, status: 'complete', pages_indexed: 1, pages_failed: 1 }).terminal).toBe(true)
    const failed = crawlSummary({ ...base, status: 'failed', error_message: 'The website does not allow this page to be indexed.' })
    expect(failed.terminal).toBe(true)
    expect(failed.text).toContain('Crawl failed')
    expect(failed.text).toContain('does not allow')
  })
})

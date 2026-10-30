import { describe, expect, it } from 'vitest'
import { crawlSummary } from './KnowledgePanel'

describe('crawlSummary', () => {
  it('reports crawl, indexing and linking progress with readable skip reasons', () => {
    const summary = crawlSummary({
      job_id: 'job', url: 'https://site.example/', status: 'complete',
      pages_found: 20, pages_fetched: 15, pages_saved: 14, pages_duplicate: 1, pages_skipped: 4,
      skipped_reasons: { robots_disallowed: 1, not_html: 1, rate_limited: 1, http_404: 1 },
      document_ids: Array.from({ length: 15 }, (_, index) => `d${index}`),
      pages_indexed: 10, pages_linked: 8, pages_failed: 0,
    })
    // A finished crawl must not read as finished ingestion.
    expect(summary.text).toContain(
      'Crawl finished; ingestion still running — found 20, fetched 15, saved 14 (+1 already saved), skipped 4',
    )
    expect(summary.text).toContain('Indexed 10 of 15 · Neo4j linked 8 of 15')
    expect(summary.text).toContain('1 blocked by robots.txt, 1 not HTML, 1 rate limited, 1 HTTP 404')
    expect(summary.terminal).toBe(false)
  })

  it('stops polling when every saved page is indexed or failed, or the crawl failed', () => {
    const base = { job_id: 'job', url: 'https://site.example/', document_ids: ['a', 'b'] }
    const done = crawlSummary({ ...base, status: 'complete', pages_indexed: 1, pages_failed: 1 })
    expect(done.terminal).toBe(true)
    expect(done.text).toContain('Crawl finished —')
    const failed = crawlSummary({ ...base, document_ids: [], status: 'failed', error_message: 'The website does not allow this page to be indexed.' })
    expect(failed.terminal).toBe(true)
    expect(failed.text).toContain('Crawl failed')
    expect(failed.text).toContain('does not allow')
  })

  it('explains pages the website refused and keeps them apart from other skips', () => {
    const message = "The website refused automated access to 27 pages (HTTP 403), so the crawl stopped. DeskMate doesn't bypass site protections."
    const summary = crawlSummary({
      job_id: 'job', url: 'https://site.example/', status: 'blocked_by_site',
      pages_found: 40, pages_fetched: 3, pages_saved: 3, pages_skipped: 28,
      skipped_reasons: { http_403: 27, not_html: 1 }, pages_blocked: { http_403: 27 },
      document_ids: ['a', 'b', 'c'], pages_indexed: 1, pages_linked: 0, pages_failed: 0,
      error_message: message,
    })
    expect(summary.text).toContain('Crawl stopped: the website refused automated access')
    expect(summary.text).toContain('Blocked by the website: 27 HTTP 403')
    expect(summary.text).toContain('Skipped: 1 not HTML')
    expect(summary.text).not.toContain('Skipped: 27')
    expect(summary.text).toContain(message)
    // Saved pages are still being ingested, so progress keeps polling.
    expect(summary.terminal).toBe(false)
  })

  it('shows Azure analysis, semantic graph and failures as separate counts', () => {
    const summary = crawlSummary({
      job_id: 'job', url: 'https://site.example/', status: 'complete',
      document_ids: ['a', 'b', 'c', 'd'], azure_analysis: 'enabled',
      pages_azure_analysed: 3, pages_indexed: 3, pages_linked: 3, pages_failed: 1,
      failed_reasons: { AZURE_DOCUMENT_CAPACITY: 1 },
      pages_semantic: { linked: 1, linked_no_relationships: 2, pending: 1 },
    })
    expect(summary.text).toContain(
      'Azure analysed 3 of 4 · indexed 3 of 4 · Neo4j linked 3 of 4 · ' +
      'semantic graph 1 of 4 (+2 entities only, no relationships) · 1 failed (1 AZURE DOCUMENT CAPACITY)',
    )
    expect(summary.terminal).toBe(true)
  })
})

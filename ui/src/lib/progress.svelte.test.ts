import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The progress store (progress.svelte.ts) is module-scoped Svelte 5 state fed
// by one EventSource on /api/events. These tests stub EventSource and drive
// the documented frame protocol: `hello` seeds/replaces the map, `data`
// frames update one job, errors flip the connected flag. Idempotent connect
// is asserted too (one EventSource per app).

type Handler = (e: { data: string }) => void

class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  listeners: Record<string, Handler[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(kind: string, fn: Handler) {
    ;(this.listeners[kind] ??= []).push(fn)
  }

  emit(kind: string, data: unknown) {
    for (const fn of this.listeners[kind] ?? []) fn({ data: JSON.stringify(data) })
    if (kind === 'message' && this.onmessage) this.onmessage({ data: JSON.stringify(data) })
  }

  open() {
    this.onopen?.()
  }

  fail() {
    this.onerror?.()
  }
}

// the store imports storedToken from ./api — localStorage is available in
// happy-dom, so the token path is real: empty by default, query-param when set

describe('progress store', () => {
  let connectEvents: typeof import('./progress.svelte').connectEvents
  let progressFor: typeof import('./progress.svelte').progressFor
  let eventsConnected: typeof import('./progress.svelte').eventsConnected

  beforeEach(async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    FakeEventSource.instances = []
    localStorage.clear()
    // a fresh module instance per test: $state is module-scoped
    vi.resetModules()
    const mod = await import('./progress.svelte')
    connectEvents = mod.connectEvents
    progressFor = mod.progressFor
    eventsConnected = mod.eventsConnected
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('connects once (idempotent) to /api/events', () => {
    connectEvents()
    connectEvents()
    connectEvents()
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/events')
    expect(eventsConnected()).toBe(false)
    FakeEventSource.instances[0].open()
    expect(eventsConnected()).toBe(true)
  })

  it('adds the token query parameter when one is stored (issue #36)', () => {
    localStorage.setItem('shrinkray.token', 's3cret')
    connectEvents()
    expect(FakeEventSource.instances[0].url).toBe('/api/events?token=s3cret')
  })

  it('hello replaces the whole map (authoritative seed)', () => {
    connectEvents()
    const es = FakeEventSource.instances[0]
    es.open()
    es.emit('hello', { jobs: { '1': { percent: 40, stage: 'probe' } } })
    expect(progressFor(1)?.percent).toBe(40)
    // a later quiet hello clears stale entries
    es.emit('hello', { jobs: {} })
    expect(progressFor(1)).toBeNull()
  })

  it('data frames update one running job', () => {
    connectEvents()
    const es = FakeEventSource.instances[0]
    es.open()
    es.emit('message', { job_id: 7, percent: 10, done: 1, total: 10 })
    es.emit('message', { job_id: 7, percent: 55, done: 6, total: 10 })
    expect(progressFor(7)?.percent).toBe(55)
    expect(progressFor(7)?.done).toBe(6)
    expect(progressFor(null)).toBeNull()
    expect(progressFor(999)).toBeNull()
  })

  it('an error flips connected off (the poll keeps the UI correct)', () => {
    connectEvents()
    const es = FakeEventSource.instances[0]
    es.open()
    expect(eventsConnected()).toBe(true)
    es.fail()
    expect(eventsConnected()).toBe(false)
  })
})

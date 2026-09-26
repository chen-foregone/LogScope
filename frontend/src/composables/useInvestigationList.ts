import { ref } from 'vue'
import { listInvestigations, type InvestigationPage } from '../api/investigations'
export type Draft = { q: string; service: string; mode: string; from: string; before: string }
export function useInvestigationList() {
  const saved = new URLSearchParams(location.hash.split('?')[1] ?? '')
  const draft = ref<Draft>({ q: saved.get('q') ?? '', service: saved.get('service_id') ?? '',
    mode: saved.get('mode') ?? 'all', from: saved.get('from') ?? '', before: saved.get('before') ?? '' })
  const data = ref<InvestigationPage | null>(null), busy = ref(false), error = ref('')
  let sequence = 0
  let applied = new URLSearchParams(saved)
  let appliedDraft = { ...draft.value }
  for (const key of ['mode', 'from', 'before']) applied.delete(key)
  function url() {
    const state = new URLSearchParams(applied)
    state.set('mode', appliedDraft.mode)
    state.set('from', appliedDraft.from)
    state.set('before', appliedDraft.before)
    history.replaceState(null, '', `${location.pathname}${location.search}#/?${state}`)
    return '#/?' + state.toString()
  }
  async function read(params: URLSearchParams, nextDraft = appliedDraft) {
    const route = location.hash
    const current = ++sequence
    busy.value = true; error.value = ''
    try {
      let result = await listInvestigations(params)
      if (current !== sequence || location.hash !== route) return
      if (!result.items.length && result.total && result.page > 1) {
        params.set('page', String(Math.ceil(result.total / result.page_size)))
        params.delete('relative_range')
        if (result.filter.updated_from && result.filter.updated_before) {
          params.set('updated_from', result.filter.updated_from); params.set('updated_before', result.filter.updated_before)
        }
        result = await listInvestigations(params)
      }
      if (current !== sequence || location.hash !== route) return
      data.value = result
      applied = new URLSearchParams({ q: result.filter.q, page: String(result.page), page_size: String(result.page_size) })
      if (result.filter.service_id) applied.set('service_id', result.filter.service_id)
      if (result.filter.updated_from && result.filter.updated_before) {
        applied.set('updated_from', result.filter.updated_from); applied.set('updated_before', result.filter.updated_before)
      }
      appliedDraft = { ...nextDraft }
      url()
    } catch (cause) {
      if (current === sequence) error.value = cause instanceof Error ? cause.message : '读取失败，请重试'
    } finally { if (current === sequence) busy.value = false }
  }
  function apply(value = draft.value) {
    const next = { ...value }
    const params = new URLSearchParams({ q: next.q.trim(), page: '1', page_size: applied.get('page_size') ?? '20' })
    if (next.service) params.set('service_id', next.service)
    if (['1h', '24h', '7d'].includes(next.mode)) params.set('relative_range', next.mode)
    if (next.mode === 'fixed') {
      const start = new Date(next.from + '+08:00'), end = new Date(next.before + '+08:00')
      if (!next.from || !next.before || !Number.isFinite(+start) || !Number.isFinite(+end) || +end <= +start) {
        invalidate(); error.value = '请填写有效的 UTC+8 起止时间，结束须晚于开始'; return
      }
      params.set('updated_from', start.toISOString()); params.set('updated_before', end.toISOString())
    }
    invalidate()
    void read(params, next)
  }
  function refresh() { apply(appliedDraft) }
  function page(number: number) { const next = new URLSearchParams(applied); next.set('page', String(number)); void read(next) }
  function initialize() { apply(appliedDraft) }
  function revalidate() { void read(new URLSearchParams(applied)) }
  function invalidate() { sequence++; busy.value = false }
  return { draft, data, busy, error, apply, refresh, page, initialize, revalidate, invalidate }
}

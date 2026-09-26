export type Summary = {
  id: string; title: string; service: string | null; status: string; severity: string
  evidence_count: number; possible_duplicate_evidence: boolean; created_at: string; updated_at: string
}
export type Overview = { investigation_count: number; active_count: number; pending_count: number; evidence_count: number }
export type Filter = { q: string; service_id: string | null; updated_from: string | null; updated_before: string | null; resolved_at: string }
export type InvestigationPage = { items: Summary[]; total: number; page: number; page_size: number; overview: Overview; filter: Filter }
export async function listInvestigations(params: URLSearchParams): Promise<InvestigationPage> {
  const response = await fetch(`/api/investigations?${params}`)
  if (!response.ok) {
    if (response.status === 401) throw new Error('登录状态已失效')
    if (response.status === 403) throw new Error('没有查看权限')
    throw new Error(response.status === 422 ? '筛选参数无效，请检查时间范围和分页' : '读取失败，请重试')
  }
  return response.json()
}

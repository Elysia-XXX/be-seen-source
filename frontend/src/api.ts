export const token = () => sessionStorage.getItem('beseen-token') || ''
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch('/api/v1' + path, {...init, headers: {'Content-Type':'application/json', Authorization:`Bearer ${token()}`, ...init.headers}})
  if (!response.ok) {
    const body = await response.json().catch(() => ({detail:'服务暂时不可用，请确认 API 已启动。'}))
    const detail = typeof body.detail === 'string' ? body.detail : (body.detail || []).map((x: {msg:string}) => x.msg).join('；')
    if (response.status === 401) {sessionStorage.removeItem('beseen-token');window.dispatchEvent(new Event('auth-expired'))}
    throw new Error(detail || `请求失败（${response.status}）`)
  }
  return response.json()
}
export const post = (body: unknown = {}) => ({method:'POST', body:JSON.stringify(body)})
export async function exportReport(id: string) {
  const response = await fetch(`/api/v1/runs/${id}/export`, {headers:{Authorization:`Bearer ${token()}`}})
  if (!response.ok) throw new Error('导出失败，请重新登录后再试。')
  const url = URL.createObjectURL(await response.blob())
  const a = document.createElement('a'); a.href = url; a.download = `被看见-${id.slice(0,8)}.json`; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

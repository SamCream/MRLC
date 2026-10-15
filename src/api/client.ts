/** Transport layer. Never embed API calls inside React components. */
export class ApiError extends Error {
  constructor(public readonly status: number, message: string, public readonly body?: unknown) { super(message); this.name = 'ApiError'; }
}
export interface ApiClientOptions { baseUrl: string; getAccessToken?: () => string | null | Promise<string | null>; fetcher?: typeof fetch; }
export class ApiClient {
  constructor(private readonly options: ApiClientOptions) {}
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = await this.options.getAccessToken?.();
    const headers = new Headers(init.headers);
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const response = await (this.options.fetcher ?? fetch)(this.options.baseUrl.replace(/\/$/, '') + path, { credentials: 'same-origin', ...init, headers });
    if (response.status === 204) return undefined as T;
    const contentType = response.headers.get('content-type') || '';
    const body: unknown = contentType.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) {
      const detail=body && typeof body==='object' && 'detail' in body ? String((body as {detail:unknown}).detail) : '';
      throw new ApiError(response.status, `Request ${init.method || 'GET'} ${path} failed${detail ? ': '+detail : ''}`, body);
    }
    return body as T;
  }
  async getBlob(path: string): Promise<Blob | null> {
    const token = await this.options.getAccessToken?.();
    const headers = new Headers();if(token)headers.set('Authorization', `Bearer ${token}`);
    const response = await (this.options.fetcher ?? fetch)(this.options.baseUrl.replace(/\/$/, '') + path,{credentials:'same-origin',headers});
    if(response.status === 204 || response.status === 404) return null;
    if(!response.ok) throw new ApiError(response.status,`GET ${path} failed`);
    return response.blob();
  }
  get<T>(path: string) { return this.request<T>(path); }
  post<T>(path: string, data?: unknown) { return this.request<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) }); }
  patch<T>(path: string, data: unknown) { return this.request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }); }
  delete<T>(path: string) { return this.request<T>(path, { method: 'DELETE' }); }
  upload<T>(path: string, file: File, metadata: Record<string, string> = {}) {
    const body = new FormData(); body.append('file', file); Object.entries(metadata).forEach(([key, value]) => body.append(key, value));
    return this.request<T>(path, { method: 'POST', body });
  }
}
export const idSegment = (id: string) => encodeURIComponent(id);
export const query = (params: Record<string, string | number | undefined>) => {
  const search = new URLSearchParams(); Object.entries(params).forEach(([key, value]) => { if (value !== undefined) search.set(key, String(value)); });
  const value = search.toString(); return value ? `?${value}` : '';
};

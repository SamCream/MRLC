import type { RuntimeEvent } from '../domain/models';
export interface Subscription { close(): void; }
/** Event schema: {id,type,projectId?,occurredAt,payload}; deduplicate by id in consumers. */
export function subscribeEvents(url: string, onEvent: (event: RuntimeEvent) => void, onError?: (error: Event) => void): Subscription {
  const source = new EventSource(url, { withCredentials: true });
  source.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data) as RuntimeEvent); }
    catch (err) { console.error('Invalid event payload', err); }
  };
  source.onerror = (e) => onError?.(e);
  return { close: () => source.close() };
}

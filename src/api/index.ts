import { ApiClient } from './client';
import { createServices } from './services';
const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
export const http = new ApiClient({ baseUrl });
export const api = createServices(http);
export type { Services } from './services';

import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_URL,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config) => {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401) {
      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken) {
        try {
          const { data } = await axios.post(`${API_URL}/v1/auth/refresh`, {
            refresh_token: refreshToken,
          });
          localStorage.setItem('access_token', data.access_token);
          localStorage.setItem('refresh_token', data.refresh_token);
          error.config.headers.Authorization = `Bearer ${data.access_token}`;
          return axios(error.config);
        } catch {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.href = '/login';
        }
      }
    }
    return Promise.reject(error);
  }
);

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authApi = {
  register: (data: { email: string; password: string; full_name: string }) =>
    api.post('/v1/auth/register', data),
  login: (data: { email: string; password: string }) =>
    api.post('/v1/auth/login', data),
  me: () => api.get('/v1/auth/me'),
  logout: () => api.post('/v1/auth/logout'),
};

// ── Workspaces ────────────────────────────────────────────────────────────────
export const workspaceApi = {
  list: () => api.get('/v1/workspaces'),
  get: (id: string) => api.get(`/v1/workspaces/${id}`),
  create: (data: { name: string; slug: string }) => api.post('/v1/workspaces', data),
  update: (id: string, data: any) => api.patch(`/v1/workspaces/${id}`, data),
  delete: (id: string) => api.delete(`/v1/workspaces/${id}`),
};

// ── Bots ──────────────────────────────────────────────────────────────────────
export const botApi = {
  list: (workspaceId: string) => api.get(`/v1/workspaces/${workspaceId}/bots`),
  get: (workspaceId: string, botId: string) =>
    api.get(`/v1/workspaces/${workspaceId}/bots/${botId}`),
  create: (workspaceId: string, data: any) =>
    api.post(`/v1/workspaces/${workspaceId}/bots`, data),
  start: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/start`),
  stop: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/stop`),
  pause: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/pause`),
  resume: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/resume`),
  runtime: (workspaceId: string, botId: string) =>
    api.get(`/v1/workspaces/${workspaceId}/bots/${botId}/runtime`),
  signals: (workspaceId: string, botId: string) =>
    api.get(`/v1/workspaces/${workspaceId}/bots/${botId}/signals`),
  orders: (workspaceId: string, botId: string) =>
    api.get(`/v1/workspaces/${workspaceId}/bots/${botId}/orders`),
  trades: (workspaceId: string, botId: string) =>
    api.get(`/v1/workspaces/${workspaceId}/bots/${botId}/trades`),
};

// ── Analytics ─────────────────────────────────────────────────────────────────
export const analyticsApi = {
  summary: (workspaceId: string, botId?: string) =>
    api.get(`/v1/workspaces/${workspaceId}/analytics/summary`, {
      params: botId ? { bot_id: botId } : {},
    }),
  equityCurve: (workspaceId: string, botId?: string) =>
    api.get(`/v1/workspaces/${workspaceId}/analytics/equity-curve`, {
      params: botId ? { bot_id: botId } : {},
    }),
};

// ── Public (unauthenticated) ──────────────────────────────────────────────────
export const publicApi = {
  strategies: () => api.get('/v1/public/strategies'),
  leaderboard: () => api.get('/v1/public/performance/leaderboard'),
};

export default api;

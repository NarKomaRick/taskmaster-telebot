import { QueryClient } from '@tanstack/react-query';
const base = import.meta.env.BASE_URL;
export const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 10000, refetchInterval: 20000, retry: 1, refetchOnWindowFocus: true }, mutations: { retry: false } } });
export async function api<T>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const upload = data instanceof FormData;
  const response = await fetch(base + 'api' + path, {
    method, headers: { Authorization: 'tma ' + (window.Telegram?.WebApp.initData || ''), ...(data && !upload ? { 'Content-Type': 'application/json' } : {}) },
    body: data === undefined ? undefined : upload ? data as FormData : JSON.stringify(data),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: 'Сервис недоступен' }));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Проверьте заполненные поля');
  }
  return response.json();
}
export async function download(id: number, name: string) {
  const response = await fetch(base + 'api/attachments/' + id, { headers: { Authorization: 'tma ' + (window.Telegram?.WebApp.initData || '') } });
  if (!response.ok) throw new Error('Не удалось загрузить файл');
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

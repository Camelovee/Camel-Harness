const API_PREFIX = "/api";

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new Error((await response.text()) || `请求失败：${response.status}`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export function apiPath(path: string): string {
  return `${API_PREFIX}${path}`;
}

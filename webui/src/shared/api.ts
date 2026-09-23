const API_PREFIX = "/api";

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = await response.text();
    let message = body || `请求失败：${response.status}`;
    try {
      const parsed: unknown = JSON.parse(body);
      if (
        typeof parsed === "object" && parsed !== null &&
        "detail" in parsed && typeof parsed.detail === "string"
      ) {
        message = parsed.detail;
      }
    } catch {
      // 非 JSON 错误仍保留原始响应。
    }
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export function apiPath(path: string): string {
  return `${API_PREFIX}${path}`;
}

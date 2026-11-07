/** HTTP transport only. No provider execution or DOM access. */
export const BRIDGE_URL = "http://127.0.0.1:8765";

export class HttpError extends Error {
  constructor(status) {
    super(`Bridge HTTP ${status}`);
    this.status = status;
  }
}

export class JobClient {
  constructor(baseUrl = BRIDGE_URL, fetchImpl = globalThis.fetch.bind(globalThis)) {
    this.baseUrl = baseUrl;
    this.fetchImpl = fetchImpl;
  }

  async receiveJob(extensionSessionId) {
    const headers = {};
    if (typeof extensionSessionId === "string" && extensionSessionId.trim()) {
      headers["X-MyTool-Extension-Session"] = extensionSessionId;
    }
    const response = await this.fetchImpl(`${this.baseUrl}/api/jobs/next`, {
      signal: AbortSignal.timeout(25000), cache: "no-store", headers,
    });
    if (response.status === 204) return null;
    if (!response.ok) throw new HttpError(response.status);
    // A 200 response means the Bridge has already atomically marked processing.
    return response.json();
  }

  async publishModelState(state, extensionSessionId) {
    const response = await this.fetchImpl(`${this.baseUrl}/api/connection/model`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-MyTool-Extension-Session": extensionSessionId,
      },
      body: JSON.stringify({ model: state.model, effort: state.effort }),
      signal: AbortSignal.timeout(10000),
      cache: "no-store",
    });
    if (!response.ok) throw new HttpError(response.status);
    return response.json();
  }
}

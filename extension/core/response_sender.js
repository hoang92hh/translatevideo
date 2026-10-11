import { BRIDGE_URL, HttpError } from "./job_client.js";

/** Reposting the same result is safe; this module never executes a job. */
export class ResponseSender {
  constructor(baseUrl = BRIDGE_URL, fetchImpl = globalThis.fetch.bind(globalThis)) {
    this.baseUrl = baseUrl;
    this.fetchImpl = fetchImpl;
  }

  async send(result, extensionSessionId) {
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/jobs/${encodeURIComponent(result.job_id)}/result`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-MyTool-Extension-Session": extensionSessionId,
        },
        body: JSON.stringify(result),
        signal: AbortSignal.timeout(10000),
      },
    );
    if (!response.ok) throw new HttpError(response.status);
    const record = await response.json();
    if (record.job_id !== result.job_id || record.status !== result.status) {
      throw new Error("Result acknowledgement does not match the submitted job.");
    }
    return record;
  }
}

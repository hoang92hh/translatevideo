import { JobClient } from "./core/job_client.js";
import { ResponseSender } from "./core/response_sender.js";
import { ProviderExecutor, ACTIVE_KEY } from "./core/provider_executor.js";
import { failure } from "./core/execution_errors.js";

const WAKE_ALARM = "mytool-transport";
const PENDING_KEY = "pendingResult";
const EXTENSION_SESSION_KEY = "extensionSessionId";
const client = new JobClient();
const sender = new ResponseSender();
const executor = new ProviderExecutor();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let running = false;
let extensionSessionPromise = null;

function extensionSessionId() {
  if (!extensionSessionPromise) {
    extensionSessionPromise = (async () => {
      const stored = await chrome.storage.session.get(EXTENSION_SESSION_KEY);
      if (typeof stored[EXTENSION_SESSION_KEY] === "string" && stored[EXTENSION_SESSION_KEY]) {
        return stored[EXTENSION_SESSION_KEY];
      }
      const sessionId = crypto.randomUUID();
      await chrome.storage.session.set({ [EXTENSION_SESSION_KEY]: sessionId });
      return sessionId;
    })();
  }
  return extensionSessionPromise;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "MYTOOL_MODEL_STATE") return;
  const state = message.state;
  if (!state || ![state.model, state.effort].every(
    value => typeof value === "string" && value.trim())) {
    sendResponse({ saved: false, error: "INVALID_MODEL_STATE" });
    return;
  }
  void extensionSessionId().then(sessionId => client.publishModelState(
    { ...state, provider: message.provider || "chatgpt" }, sessionId,
  )).then(() => {
    sendResponse({ saved: true });
  }).catch((error) => {
    console.warn("[Bridge][MODEL_STATE_SAVE_ERROR]", error.message);
    sendResponse({ saved: false, error: error.message });
  });
  return true;
});

export async function runLoop() {
  if (running) return;
  running = true;
  let delay = 5000;
  try {
    while (true) {
      try {
        const stored = await chrome.storage.session.get([PENDING_KEY, ACTIVE_KEY]);
        let result = stored[PENDING_KEY];
        if (!result) {
          if (stored[ACTIVE_KEY]) {
            const active = stored[ACTIVE_KEY];
            try { result = await executor.resume(active); }
            catch (error) { result = failure(active.job_id, error); }
          } else {
            const job = await client.receiveJob(await extensionSessionId());
            if (!job) { delay = 5000; continue; }
            console.info("[JOB]", job.job_id, "JOB RECEIVED (processing)");
            await chrome.storage.session.set({
              [ACTIVE_KEY]: { job_id: job.job_id, tabId: null, startedAt: Date.now() },
            });
            try { result = await executor.execute(job); }
            catch (error) { result = failure(job.job_id, error); }
          }
          await chrome.storage.session.set({ [PENDING_KEY]: result });
        }
        // Retry only this saved result, never the provider or prompt submission.
        await sender.send(result, await extensionSessionId());
        await chrome.storage.session.remove([PENDING_KEY, ACTIVE_KEY]);
        console.info("[JOB]", result.job_id, result.status.toUpperCase(), result.error || "", result.message || "");
        delay = 5000;
      } catch (error) {
        console.warn("[Bridge]", error.message, `Retry transport in ${delay / 1000}s`);
        await sleep(delay);
        delay = Math.min(delay * 2, 30000);
      }
    }
  } finally { running = false; }
}

async function start() {
  try {
    if (!(await chrome.alarms.get(WAKE_ALARM))) {
      await chrome.alarms.create(WAKE_ALARM, { periodInMinutes: 0.5 });
    }
    await runLoop();
  } catch (error) {
    console.warn("[Bridge] Startup failed; next alarm will retry:", error.message);
  }
}
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === WAKE_ALARM) void start();
});
chrome.runtime.onInstalled.addListener(() => { void start(); });
chrome.runtime.onStartup.addListener(() => { void start(); });
void start();

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
const responseDownloads = new Map();

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function baseName(value) {
  return String(value || "").split(/[\\/]/u).at(-1) || "";
}

function matchesExpectedDownload(value, expectedName) {
  const actual = baseName(value);
  const dot = expectedName.lastIndexOf(".");
  const stem = dot > 0 ? expectedName.slice(0, dot) : expectedName;
  const suffix = dot > 0 ? expectedName.slice(dot) : "";
  return new RegExp(
    `^${escapeRegExp(stem)}(?: \\(\\d+\\))?${escapeRegExp(suffix)}$`, "i",
  ).test(actual);
}

function downloadMatches(item, expectedName) {
  return [item.filename, item.url, item.finalUrl].some(
    value => matchesExpectedDownload(value, expectedName),
  );
}

function updateDownloadRecord(record, item) {
  record.downloadId = item.id;
  record.path = item.filename || record.path;
  if (item.state === "interrupted") {
    record.status = "error";
    record.message = `Chrome interrupted the download of ${record.expectedName}.`;
    return;
  }
  if (item.state === "complete") {
    record.status = record.path ? "complete" : "error";
    if (!record.path) {
      record.message = `Chrome did not expose the path for ${record.expectedName}.`;
    }
    return;
  }
  record.status = "downloading";
}

async function reconcileResponseDownload(record) {
  if (record.status === "complete" || record.status === "error") return;
  const items = await chrome.downloads.search({
    startedAfter: new Date(record.createdAt - 1).toISOString(),
    orderBy: ["-startTime"],
    limit: 50,
  });
  const matches = items.filter(item => {
    const startedAt = Date.parse(item.startTime || "");
    return Number.isFinite(startedAt)
      && startedAt >= record.createdAt
      && downloadMatches(item, record.expectedName);
  });
  if (!matches.length) return;
  updateDownloadRecord(record, matches[0]);
}

chrome.downloads.onCreated.addListener((item) => {
  const candidates = [...responseDownloads.values()].filter(record =>
    record.status === "waiting"
    && Date.now() - record.createdAt <= 120000
    && downloadMatches(item, record.expectedName)
  );
  if (candidates.length !== 1) return;
  updateDownloadRecord(candidates[0], item);
});

chrome.downloads.onChanged.addListener((delta) => {
  const record = [...responseDownloads.values()].find(
    item => item.downloadId === delta.id,
  );
  if (!record) return;
  if (delta.filename?.current) record.path = delta.filename.current;
  if (delta.state?.current === "interrupted") {
    record.status = "error";
    record.message = `Chrome interrupted the download of ${record.expectedName}.`;
    return;
  }
  if (delta.state?.current !== "complete") return;
  chrome.downloads.search({ id: delta.id }, (items) => {
    const item = items?.[0];
    if (!item || !downloadMatches(item, record.expectedName)) {
      record.status = "error";
      record.message = `Completed download did not match ${record.expectedName}.`;
      return;
    }
    updateDownloadRecord(record, item);
  });
});

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
  if (sender.id !== chrome.runtime.id) return;
  if (message?.type === "MYTOOL_PREPARE_RESPONSE_DOWNLOAD") {
    const expectedName = String(message.expected_name || "").trim();
    if (!Number.isInteger(sender.tab?.id) || !/^step3-translation-result-\d+-\d+\.json$/u.test(expectedName)) {
      sendResponse({ ready: false, message: "Invalid response download request." });
      return;
    }
    const existing = [...responseDownloads.values()].find(record =>
      record.tabId === sender.tab.id && record.status !== "complete" && record.status !== "error"
    );
    if (existing) {
      sendResponse({ ready: false, message: "This tab already has an active response download." });
      return;
    }
    const token = crypto.randomUUID();
    responseDownloads.set(token, {
      token,
      tabId: sender.tab.id,
      expectedName,
      createdAt: Date.now(),
      downloadId: null,
      status: "waiting",
      path: "",
      message: "",
    });
    sendResponse({ ready: true, token });
    return;
  }
  if (message?.type === "MYTOOL_RESPONSE_DOWNLOAD_STATUS") {
    const token = String(message.token || "");
    const record = responseDownloads.get(token);
    if (!record || record.tabId !== sender.tab?.id) {
      sendResponse({ status: "error", message: "Response download tracking was lost." });
      return;
    }
    void reconcileResponseDownload(record).then(() => {
      if (Date.now() - record.createdAt > 120000 && record.status !== "complete") {
        record.status = "error";
        record.message = `Timed out waiting for ${record.expectedName}.`;
      }
      sendResponse({ status: record.status, path: record.path, message: record.message });
      if (record.status === "complete" || record.status === "error") {
        responseDownloads.delete(token);
      }
    }).catch((error) => {
      responseDownloads.delete(token);
      sendResponse({
        status: "error",
        message: `Could not inspect Chrome downloads for ${record.expectedName}: ${error.message}`,
      });
    });
    return true;
  }
  if (message?.type === "MYTOOL_CANCEL_RESPONSE_DOWNLOAD") {
    const token = String(message.token || "");
    const record = responseDownloads.get(token);
    if (record?.tabId === sender.tab?.id) responseDownloads.delete(token);
    sendResponse({ cancelled: true });
    return;
  }
  if (message?.type !== "MYTOOL_MODEL_STATE") return;
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

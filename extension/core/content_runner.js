import { createProject, updateProjectInstructions } from "../providers/chatgpt/project_actions.js";
import { replaceProjectSources } from "../providers/chatgpt/project_source_actions.js";
import { chatgptSelectors as S } from "../providers/chatgpt/chatgpt_selectors.js";
import { claudeSelectors as ClaudeS } from "../providers/claude/claude_selectors.js";
import { ProviderFactory } from "./provider_factory.js";
import { failure, executionError } from "./execution_errors.js";

const jobs = new Map();
let activeJob = null;
let projectChatOpening = false;
const factory = new ProviderFactory();
const pageProvider = location.hostname === "claude.ai" ? "claude" : "chatgpt";
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let modelStateReadRunning = false;
let modelStateReadQueued = false;
let lastModelStateUrl = location.href;
let navigationReadTimer = null;
let firstModelStateReadComplete = false;

async function waitForModelStateReady(provider, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let composerError = null;
  do {
    try {
      if (await provider.isReady()) return true;
    } catch (error) {
      if (error?.code !== "COMPOSER_NOT_FOUND") throw error;
      composerError = error;
    }
    await pause(250);
  } while (Date.now() < deadline);
  if (composerError) throw composerError;
  return false;
}

async function publishModelState(state) {
  try {
    const result = await chrome.runtime.sendMessage({
      type: "MYTOOL_MODEL_STATE", provider: pageProvider, state,
    });
    if (!result?.saved) {
      console.warn("[MyTool][MODEL_STATE_SAVE_ERROR]", result?.error || "Bridge did not save the state.");
      return false;
    }
    console.info("[MyTool][MODEL_STATE_SAVED]", state);
    return true;
  } catch (error) {
    console.warn("[MyTool][MODEL_STATE_SAVE_ERROR]", error?.message || String(error));
    return false;
  }
}

async function logCurrentModelState(reason) {
  if (pageProvider !== "chatgpt") return false;
  try {
    console.info("[MyTool][MODEL_STATE_REASON]", reason);
    const provider = factory.create("chatgpt", {
      log: step => console.info("[MyTool][MODEL_STATE_STEP]", step),
    });
    if (!(await waitForModelStateReady(provider))) {
      console.warn("[MyTool][MODEL_STATE_ERROR] ChatGPT is not ready.");
      return false;
    }
    const state = await provider.getModelState();
    console.info("[MyTool][MODEL_STATE]", state);
    await publishModelState(state);
    return true;
  } catch (error) {
    console.warn("[MyTool][MODEL_STATE_ERROR]", error?.code || "PROVIDER_ERROR", error?.message || String(error));
    return false;
  }
}

async function requestModelStateRead(reason) {
  if (modelStateReadRunning) {
    modelStateReadQueued = true;
    return;
  }
  modelStateReadRunning = true;
  let currentReason = reason;
  try {
    do {
      modelStateReadQueued = false;
      const readSucceeded = await logCurrentModelState(currentReason);
      if (!firstModelStateReadComplete && readSucceeded) {
        firstModelStateReadComplete = true;
        lastModelStateUrl = location.href;
        clearTimeout(navigationReadTimer);
        navigationReadTimer = null;
        modelStateReadQueued = false;
        console.info("[MyTool][MODEL_STATE_NAVIGATION_COALESCED]", lastModelStateUrl);
      }
      currentReason = "queued-navigation";
    } while (modelStateReadQueued);
  } finally {
    modelStateReadRunning = false;
  }
}

function scheduleModelStateRead(reason) {
  if (pageProvider !== "chatgpt") return;
  clearTimeout(navigationReadTimer);
  navigationReadTimer = setTimeout(() => {
    navigationReadTimer = null;
    void requestModelStateRead(reason);
  }, 1000);
}

// Tạm tắt đọc model/effort tự động khi URL thay đổi trong ChatGPT.
// setInterval(() => {
//   const currentUrl = location.href;
//   if (currentUrl === lastModelStateUrl) return;
//   lastModelStateUrl = currentUrl;
//   scheduleModelStateRead("navigation");
// }, 500);

async function execute(job, record) {
  try {
    if (job.action === "configure_project_from_folder") {
      const log = step => { record.steps.push(step); console.info("[JOB]", job.job_id, step); };
      await replaceProjectSources(job.source_files, {log});
      log("UPDATING PROJECT INSTRUCTIONS");
      try {
        await updateProjectInstructions(job.content);
      } catch (error) {
        error.message = "AGENTS.md and SKILL.md were replaced, but Project instructions " +
          "were not confirmed. " + error.message;
        throw error;
      }
      log("PROJECT CONFIGURATION COMPLETE");
      record.result = { job_id: job.job_id, status: "completed",
        text: "Đã thay thế AGENTS.md, SKILL.md và cập nhật Project instructions." };
      return;
    }
    if (job.action === "update_project_instructions") {
      await updateProjectInstructions(job.content);
      record.result = { job_id: job.job_id, status: "completed",
        text: "Đã cập nhật Project instructions từ file Markdown." };
      return;
    }
    const provider = factory.create(job.provider, {
      log: (step) => { record.steps.push(step); console.info("[JOB]", job.job_id, step); },
      timeoutMs: job.options?.response_file_name ? 600000 : undefined,
    });
    if (!(await provider.isReady())) {
      throw executionError(
        "PROVIDER_NOT_READY",
        `Log in and leave ${job.provider === "claude" ? "Claude" : "ChatGPT"} idle before starting.`,
      );
    }
    if (job.action === "sync_model") {
      const selection = await provider.selectModel(job.model, job.effort);
      if (!await publishModelState({ model: selection.model, effort: selection.effort })) {
        throw executionError(
          "PROVIDER_ERROR",
          "ChatGPT model was updated, but the new state could not be saved to MyTool.",
        );
      }
      record.result = { job_id: job.job_id, status: "completed",
        text: (selection.changed ? "Đã đồng bộ: " : "Đã khớp: ") + selection.model + " / " + selection.effort };
      return;
    }
    await provider.setPrompt(job.content, job.options);
    await provider.sendPrompt();
    await provider.waitForResponse();
    const response = await provider.getResponse();
    record.result = {
      job_id: job.job_id,
      status: "completed",
      ...(typeof response === "string" ? { text: response } : response),
    };
  } catch (error) {
    record.result = failure(job.job_id, error);
  } finally {
    record.status = record.result.status;
    activeJob = null;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !message?.type?.startsWith("MYTOOL_")) return;
  if (message.type === "MYTOOL_PING") {
    sendResponse({ ready: true, url: location.href });
  } else if (message.type === "MYTOOL_PROJECT_READY") {
    const selectors = pageProvider === "claude" ? ClaudeS : S;
    const composer = [...document.querySelectorAll(selectors.composer)].find(node =>
      node.getClientRects().length > 0 &&
      (node.isContentEditable || node.tagName === "TEXTAREA") &&
      !node.disabled && node.getAttribute("aria-disabled") !== "true");
    if (composer) {
      sendResponse({ ready: true, url: location.href });
      return;
    }
    if (!projectChatOpening && /\/(?:project|projects)(?:\/[^/]+)?\/?$/.test(location.pathname)) {
      const start = [...document.querySelectorAll("main button, main a")].find(node =>
        node.getClientRects().length > 0 &&
        /(new\s+chat|start.*chat|new\s+conversation|tr\u00f2\s+chuy\u1ec7n\s+m\u1edbi)/i
          .test((node.innerText || node.getAttribute("aria-label") || "").trim()));
      if (start) {
        projectChatOpening = true;
        start.click();
        sendResponse({ ready: false, openingChat: true, url: location.href });
        return;
      }
    }
    sendResponse({ ready: false, url: location.href });
  } else if (message.type === "MYTOOL_STATUS") {
    const record = jobs.get(message.job_id);
    sendResponse(record ? { job_id: message.job_id, status: record.status, result: record.result, steps: record.steps }
      : { error: "EXECUTION_STATE_LOST", message: "No execution state in this document. Prompt will not be resent." });
  } else if (message.type === "MYTOOL_CREATE_PROJECT") {
    if (activeJob !== null) {
      sendResponse({ error: "PROVIDER_NOT_READY", message: "This tab is already processing a job." });
      return;
    }
    activeJob = "create-project";
    void createProject(message.name).then(() => {
      sendResponse({ accepted: true });
    }).catch(error => {
      console.error("[MyTool] Project creation failed:", error);
      sendResponse({
        error: error?.code || "PROJECT_CREATE_FAILED",
        message: error?.message || "ChatGPT project creation failed.",
      });
    }).finally(() => { activeJob = null; });
    return true; // Keep the response channel open until UI automation succeeds or fails.
  } else if (message.type === "MYTOOL_EXECUTE") {
    const job = message.job;
    if (!job || typeof job.job_id !== "string"
        || !["prompt", "sync_model", "update_project_instructions",
          "configure_project_from_folder"].includes(job.action)) {
      sendResponse({ error: "INVALID_REQUEST", message: "Invalid content-script job." });
      return;
    }
    if (job.action === "sync_model" && (![job.model, job.effort].every(
      value => typeof value === "string" && value.trim()))) {
      sendResponse({ error: "INVALID_REQUEST", message: "Choose both model and reasoning level." });
      return;
    }
    const signature = JSON.stringify(job);
    const existing = jobs.get(job.job_id);
    if (existing) {
      sendResponse(existing.signature === signature
        ? { accepted: true, job_id: job.job_id }
        : { error: "DUPLICATE_EXECUTION", message: "Job ID already used by a different request." });
      return; // Never execute twice, even if the earlier result is an error.
    }
    if (activeJob !== null) {
      sendResponse({ error: "PROVIDER_NOT_READY", message: "This tab is already processing a job." });
      return;
    }
    const record = { signature, status: "processing", result: null, steps: [] };
    jobs.set(job.job_id, record);
    activeJob = job.job_id;
    sendResponse({ accepted: true, job_id: job.job_id });
    void execute(job, record);
  }
});

// Tạm tắt đọc model/effort tự động khi reload trang.
// if (pageProvider === "chatgpt") scheduleModelStateRead("initial-load");

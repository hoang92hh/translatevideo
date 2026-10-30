import { chatgptTarget } from "../providers/chatgpt/chatgpt_config.js";
import { claudeTarget } from "../providers/claude/claude_config.js";
import { executionError, failure } from "./execution_errors.js";

const targets = new Map([
  ["chatgpt", chatgptTarget],
  ["claude", claudeTarget],
]);
export const ACTIVE_KEY = "activeExecution";
const PROJECT_BINDINGS_KEY = "projectConversationBindings";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function message(tabId, body, timeoutMs = 5000) {
  let timer;
  try {
    return await Promise.race([
      chrome.tabs.sendMessage(tabId, body, { frameId: 0 }),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(executionError(
          "CONTENT_SCRIPT_NOT_READY", "Content script did not acknowledge the message.",
        )), timeoutMs);
      }),
    ]);
  } finally { clearTimeout(timer); }
}

function isProviderUrl(url) {
  try {
    const origin = new URL(url).origin;
    return [...targets.values()].some(target => target.origin === origin);
  } catch {
    return false;
  }
}

async function restoreContentScript(tabId) {
  const tab = await chrome.tabs.get(tabId);
  if (tab.status !== "complete" || !isProviderUrl(tab.url)) return false;
  await chrome.scripting.executeScript({
    target: { tabId, frameIds: [0] },
    files: ["content.js"],
  });
  console.info("[Bridge] Content script restored in provider tab", tabId);
  return true;
}

async function ready(tabId, expectedRoot = null) {
  let restored = false;
  for (let i = 0; i < 40; i++) {
    try {
      const tab = await chrome.tabs.get(tabId);
      const ping = await message(tabId, { type: "MYTOOL_PING" });
      if (ping?.ready && tab.status === "complete" &&
          (!expectedRoot || ping.url === expectedRoot || ping.url?.startsWith(expectedRoot + "/"))) return;
    } catch {
      if (!restored) {
        try {
          restored = await restoreContentScript(tabId);
          if (restored) await sleep(250);
        } catch {}
      }
    }
    await sleep(500);
  }
  throw executionError("CONTENT_SCRIPT_NOT_READY", "Reload the provider tab after loading the extension.");
}

async function projectComposerReady(tabId, job) {
  let authorizedProjectChatOpening = false;
  for (let i = 0; i < 40; i++) {
    try {
      const state = await message(tabId, { type: "MYTOOL_PROJECT_READY" });
      if (state?.openingChat && await projectMatchesUrl(state.url, job, tabId)) {
        authorizedProjectChatOpening = true;
      }
      if (state?.ready) {
        if (await projectMatchesUrl(state.url, job, tabId)) return;
        const target = targets.get(job.provider);
        if (authorizedProjectChatOpening && new URL(state.url).origin === target.origin) {
          await saveProjectBinding({
            tabId,
            provider: job.provider,
            projectUrl: job.project_url,
          });
          return;
        }
      }
    } catch {}
    await sleep(500);
  }
  throw executionError("COMPOSER_NOT_FOUND", "Project opened, but its chat composer did not become ready.");
}

function projectFromUrl(url, name) {
  try {
    const parsed = new URL(url);
    if (parsed.origin !== "https://chatgpt.com") return null;
    const match = parsed.pathname.match(/^\/g\/(g-p-[^/]+)\/project\/?$/);
    return match ? { id: match[1], name, url: parsed.origin + parsed.pathname } : null;
  } catch { return null; }
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^$\{\}()|[\]\\]/g, "\\$&");
}

async function projectMatchesUrl(url, job, tabId) {
  try {
    const parsed = new URL(url);
    const target = targets.get(job.provider);
    if (!target || parsed.origin !== target.origin) return false;
    if (!job.project_url) {
      return parsed.pathname === "/" || /^\/c\/[^/]+\/?$/.test(parsed.pathname);
    }
    if (job.conversation_mode === "continue") {
      const stored = await chrome.storage.session.get(PROJECT_BINDINGS_KEY);
      const binding = stored[PROJECT_BINDINGS_KEY]?.[job.provider];
      return binding?.tabId === tabId && binding.projectUrl === job.project_url &&
        binding.conversationUrl === url;
    }
    const projectPath = new URL(job.project_url).pathname.replace(/\/$/, "");
    const currentPath = parsed.pathname.replace(/\/$/, "");
    if (currentPath === projectPath) return true;
    if (job.provider === "chatgpt") {
      const projectId = projectPath.match(/^\/g\/(g-p-[^/]+)\/project$/)?.[1];
      if (projectId) {
        const conversationPattern = new RegExp(
          "^/g/" + escapeRegExp(projectId) + "(?:-[^/]+)?/c/[^/]+/?$",
        );
        if (conversationPattern.test(parsed.pathname)) return true;
      }
    } else {
      const projectId = projectPath.match(/^\/(?:project|projects)\/([^/]+)/)?.[1];
      const currentProjectId = currentPath.match(/^\/(?:project|projects)\/([^/]+)/)?.[1];
      if (projectId && currentProjectId === projectId) return true;
    }
    return false;
  } catch { return false; }
}

async function saveProjectBinding(active) {
  if (!active.projectUrl) return;
  const tab = await chrome.tabs.get(active.tabId);
  const stored = await chrome.storage.session.get(PROJECT_BINDINGS_KEY);
  const bindings = stored[PROJECT_BINDINGS_KEY] || {};
  bindings[active.provider] = {
    tabId: active.tabId,
    projectUrl: active.projectUrl,
    conversationUrl: tab.url,
  };
  await chrome.storage.session.set({ [PROJECT_BINDINGS_KEY]: bindings });
}

async function clearProjectBinding(provider) {
  const stored = await chrome.storage.session.get(PROJECT_BINDINGS_KEY);
  const bindings = stored[PROJECT_BINDINGS_KEY] || {};
  if (!(provider in bindings)) return;
  delete bindings[provider];
  await chrome.storage.session.set({ [PROJECT_BINDINGS_KEY]: bindings });
}

async function boundConversationUrl(job, tabId) {
  if (!job.project_url) return null;
  const stored = await chrome.storage.session.get(PROJECT_BINDINGS_KEY);
  const binding = stored[PROJECT_BINDINGS_KEY]?.[job.provider];
  if (binding?.tabId !== tabId || binding.projectUrl !== job.project_url) return null;
  try {
    const target = targets.get(job.provider);
    return new URL(binding.conversationUrl).origin === target.origin
      ? binding.conversationUrl : null;
  } catch { return null; }
}

async function navigate(tabId, url) {
  const tab = await chrome.tabs.get(tabId);
  if (tab.url !== url) await chrome.tabs.update(tabId, { url, active: true });
}

async function openPromptTarget(tabId, job) {
  const tab = await chrome.tabs.get(tabId);
  const continuing = job.conversation_mode === "continue";
  const keepCurrent = continuing && await projectMatchesUrl(tab.url, job, tabId);
  if (!keepCurrent) {
    const targetConfig = targets.get(job.provider);
    let target = job.project_url || targetConfig.home;
    if (continuing && job.project_url) {
      target = await boundConversationUrl(job, tabId);
      if (!target) {
        throw executionError(
          "PROJECT_CONVERSATION_LOST",
          "The conversation created for this Step 3 run is no longer available; prompt was not sent.",
        );
      }
    }
    if (!continuing) await clearProjectBinding(job.provider);
    await navigate(tabId, target);
    await ready(tabId, targetConfig.origin);
  } else {
    await ready(tabId);
  }
  if (job.project_url) await projectComposerReady(tabId, job);
}

async function chooseTab(job) {
  const target = targets.get(job.provider);
  if (!target) throw executionError("UNSUPPORTED_PROVIDER", "Provider is not available.");
  const tabs = await chrome.tabs.query({ url: target.matches });
  if (tabs.length > 1) {
    const active = tabs.filter(tab => tab.active);
    if (active.length !== 1) {
      throw executionError(target.multipleTabsCode, `Select one ${job.provider} tab or close extra tabs.`);
    }
    return active[0].id;
  }
  if (tabs.length === 1) return tabs[0].id;
  if (job.project_url || job.action === "create_project" || job.action === "prompt") {
    const url = job.action === "create_project"
      ? "https://chatgpt.com/projects" : job.project_url || target.home;
    const tab = await chrome.tabs.create({ url, active: true });
    return tab.id;
  }
  throw executionError(target.missingTabCode, `Open one logged-in ${job.provider} tab first.`);
}

export class ProviderExecutor {
  async execute(job) {
    if (!targets.has(job.provider)) throw executionError("UNSUPPORTED_PROVIDER", "Provider is not available.");
    if (job.provider !== "chatgpt" && job.action !== "prompt") {
      throw executionError("UNSUPPORTED_ACTION", "This provider supports prompt jobs only.");
    }
    const targetConfig = targets.get(job.provider);
    const tabId = await chooseTab(job);
    console.info("[JOB]", job.job_id, targetConfig.foundLog);
    if (job.action === "create_project") {
      const tab = await chrome.tabs.get(tabId);
      if (!tab.url?.startsWith("https://chatgpt.com/projects")) {
        await chrome.tabs.update(tabId, { url: "https://chatgpt.com/projects", active: true });
      }
      await ready(tabId, "https://chatgpt.com/projects");
    } else if (job.action === "update_project_instructions"
        || job.action === "configure_project_from_folder") {
      const target = job.action === "configure_project_from_folder"
        ? job.project_url + "?tab=sources" : job.project_url;
      await navigate(tabId, target);
      await ready(tabId, job.project_url.replace(/\/project\/?$/, ""));
    } else if (job.action === "prompt") {
      await openPromptTarget(tabId, job);
    } else if (job.project_url) {
      const tab = await chrome.tabs.get(tabId);
      const root = job.project_url.replace(/\/project\/?$/, "");
      if (!tab.url?.startsWith(root + "/")) {
        await chrome.tabs.update(tabId, { url: job.project_url, active: true });
      }
      await ready(tabId, root);
      await projectComposerReady(tabId, job);
    } else {
      await ready(tabId);
    }
    const initialTab = job.action === "create_project" ? await chrome.tabs.get(tabId) : null;
    const active = { job_id: job.job_id, tabId, startedAt: Date.now(),
      action: job.action, projectName: job.action === "create_project" ? job.content : null,
      provider: job.provider, projectUrl: job.project_url || "",
      responseFileName: String(job.options?.response_file_name || ""),
      initialProjectId: initialTab ? projectFromUrl(initialTab.url, job.content)?.id : null };
    await chrome.storage.session.set({ [ACTIVE_KEY]: active });
    if (job.action === "create_project") {
      try {
        const ack = await message(tabId, { type: "MYTOOL_CREATE_PROJECT", name: job.content }, 40000);
        if (ack?.error) throw executionError(ack.error, ack.message);
      } catch (error) {
        if (error.code && error.code !== "CONTENT_SCRIPT_NOT_READY") throw error;
      }
      return this.resume(active);
    }
    try {
      const ack = await message(tabId, { type: "MYTOOL_EXECUTE", job });
      if (ack?.error) throw executionError(ack.error, ack.message);
    } catch (error) {
      if (error.code && error.code !== "CONTENT_SCRIPT_NOT_READY") throw error;
      console.warn("[JOB]", job.job_id, "Dispatch acknowledgement lost; checking status only.");
    }
    return this.resume(active);
  }

  async resume(active) {
    if (!Number.isInteger(active.tabId)) {
      throw executionError("EXECUTION_STATE_LOST", "Worker stopped before dispatch was recorded.");
    }
    if (active.action === "create_project") {
      while (Date.now() - active.startedAt < 90000) {
        const tab = await chrome.tabs.get(active.tabId).catch(() => null);
        if (!tab) throw executionError("EXECUTION_STATE_LOST", "Project tab was closed.");
        const project = projectFromUrl(tab.url, active.projectName);
        if (project && project.id !== active.initialProjectId) return { job_id: active.job_id, status: "completed", text: "", project };
        await sleep(500);
      }
      throw executionError("PROJECT_CREATE_FAILED", "Could not confirm the new project URL. Check ChatGPT.");
    }
    let loggedSteps = 0;
    const executionTimeout = active.action === "configure_project_from_folder"
      ? 420000 : active.provider === "chatgpt" || active.responseFileName ? 620000 : 200000;
    while (Date.now() - active.startedAt < executionTimeout) {
      let record;
      try { record = await message(active.tabId, { type: "MYTOOL_STATUS", job_id: active.job_id }); }
      catch { throw executionError("EXECUTION_STATE_LOST", "Tab closed, reloaded or content script unavailable; prompt will not be resent."); }
      if (record?.error) throw executionError(record.error, record.message);
      if (record?.job_id !== active.job_id) throw executionError("EXECUTION_STATE_LOST", "Status job ID mismatch.");
      for (const step of (record.steps || []).slice(loggedSteps)) console.info("[JOB]", active.job_id, step);
      loggedSteps = (record.steps || []).length;
      if (record.result) {
        if (record.result.job_id !== active.job_id) throw executionError("EXECUTION_STATE_LOST", "Result job ID mismatch.");
        if (record.result.status === "completed") await saveProjectBinding(active);
        return record.result;
      }
      await sleep(1000);
    }
    return failure(active.job_id, executionError(
      "RESPONSE_TIMEOUT",
      "Content execution exceeded the " + Math.round(executionTimeout / 1000) +
        "-second transport deadline.",
    ));
  }
}

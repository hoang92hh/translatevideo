/** Replace named files in the Sources tab of a ChatGPT project. */
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const sourceError = (code, message) => Object.assign(new Error(message), {code});

function visible(node) {
  return !!node && node.isConnected && node.getClientRects().length > 0;
}

function enabled(node) {
  return visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true";
}

function normalizedLabel(node) {
  return (node?.innerText || node?.getAttribute?.("aria-label") || "")
    .replace(/\s+/g, " ").trim();
}

async function waitFor(find, code, message, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = find();
    if (value) return value;
    await pause(150);
  }
  throw sourceError(code, message);
}

function projectSourcesPage() {
  return /^\/g\/g-p-[^/]+\/project\/?$/.test(location.pathname)
    && new URLSearchParams(location.search).get("tab") === "sources";
}

function sourceCards(name) {
  const expected = "Preview " + name;
  return [...document.querySelectorAll("li")].filter(card =>
    [...card.querySelectorAll('button[aria-label]')]
      .some(button => button.getAttribute("aria-label") === expected));
}

function actionsButton(card, name) {
  const expected = "Actions for " + name;
  const candidates = [...card.querySelectorAll('button[aria-label]')]
    .filter(button => button.getAttribute("aria-label") === expected)
    .filter(enabled);
  if (candidates.length > 1) {
    throw sourceError("PROJECT_SOURCE_DELETE_FAILED",
      "ChatGPT exposed more than one Actions button for " + name + ".");
  }
  return candidates[0] || null;
}

function openDeleteItem() {
  const menus = [...document.querySelectorAll('[role="menu"][data-state="open"], [role="menu"]')]
    .filter(visible);
  const candidates = menus.flatMap(menu => [...menu.querySelectorAll('[role="menuitem"]')])
    .filter(enabled)
    .filter(item => /^(delete|xóa)$/i.test(normalizedLabel(item)));
  if (candidates.length > 1) {
    throw sourceError("PROJECT_SOURCE_DELETE_FAILED",
      "ChatGPT exposed more than one Delete source action.");
  }
  return candidates[0] || null;
}

async function deleteOneSource(card, name) {
  const action = await waitFor(() => actionsButton(card, name),
    "PROJECT_SOURCE_DELETE_FAILED", "The Actions button for " + name + " was not found.");
  action.click();
  const deleteItem = await waitFor(openDeleteItem, "PROJECT_SOURCE_DELETE_FAILED",
    "The Delete action for " + name + " did not appear.");
  deleteItem.click();
  await waitFor(() => !card.isConnected || !sourceCards(name).includes(card),
    "PROJECT_SOURCE_DELETE_FAILED", name + " did not disappear after Delete.", 30000);
}

async function deleteExistingSources(names, log) {
  const deleted = [];
  try {
    for (const name of names) {
      let count = 0;
      while (sourceCards(name).length) {
        if (++count > 20) {
          throw sourceError("PROJECT_SOURCE_DELETE_FAILED",
            "Too many existing sources named " + name + ".");
        }
        await deleteOneSource(sourceCards(name)[0], name);
        deleted.push(name);
        log("DELETED SOURCE " + name);
      }
    }
  } catch (error) {
    if (deleted.length) {
      error.message = "Removed existing " + [...new Set(deleted)].join(", ") +
        ", but source cleanup did not complete. " + error.message;
    }
    throw error;
  }
  return deleted;
}

function uploadInput() {
  const candidates = [...document.querySelectorAll(
    '[data-chatgpt-file-drop-target="true"] input[type="file"][multiple]',
  )].filter(input => input.isConnected && !input.disabled);
  if (candidates.length > 1) {
    throw sourceError("PROJECT_SOURCE_UPLOAD_FAILED",
      "ChatGPT exposed more than one project source file input.");
  }
  return candidates[0] || null;
}

function readySource(name) {
  return sourceCards(name).find(card => {
    const preview = [...card.querySelectorAll('button[aria-label]')].find(
      button => button.getAttribute("aria-label") === "Preview " + name,
    );
    return preview && !preview.disabled && preview.getAttribute("aria-disabled") !== "true"
      && !card.querySelector('[aria-busy="true"]');
  }) || null;
}

async function waitForUploadedSources(names, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let stableChecks = 0;
  while (Date.now() < deadline) {
    if (names.every(name => readySource(name))) {
      stableChecks += 1;
      if (stableChecks >= 3) return;
    } else {
      stableChecks = 0;
    }
    await pause(250);
  }
  const missing = names.filter(name => !readySource(name));
  throw sourceError("PROJECT_SOURCE_UPLOAD_TIMEOUT",
    "Upload was not confirmed for: " + missing.join(", ") +
    ". Files were not uploaded again automatically.");
}

async function uploadSources(sourceFiles, timeoutMs, log) {
  const input = await waitFor(uploadInput, "PROJECT_SOURCE_UPLOAD_FAILED",
    "The project source file input was not found.", 20000);
  const transfer = new window.DataTransfer();
  for (const source of sourceFiles) {
    transfer.items.add(new window.File([source.content], source.name, {
      type:"text/markdown", lastModified:Date.now(),
    }));
  }
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "files",
  )?.set;
  if (!setter) {
    throw sourceError("PROJECT_SOURCE_UPLOAD_FAILED",
      "The browser does not allow assigning project source files.");
  }
  setter.call(input, transfer.files);
  if (input.files?.length !== sourceFiles.length) {
    throw sourceError("PROJECT_SOURCE_UPLOAD_FAILED",
      "ChatGPT file input did not receive both source files.");
  }
  input.dispatchEvent(new window.Event("input", {bubbles:true}));
  input.dispatchEvent(new window.Event("change", {bubbles:true}));
  log("SOURCE UPLOAD STARTED");
  await waitForUploadedSources(sourceFiles.map(source => source.name), timeoutMs);
  for (const source of sourceFiles) log("UPLOADED SOURCE " + source.name);
}

export async function replaceProjectSources(sourceFiles, {
  timeoutMs = 300000,
  log = () => {},
} = {}) {
  if (!projectSourcesPage()) {
    throw sourceError("PROJECT_SOURCE_UPLOAD_FAILED",
      "ChatGPT is not on the selected project's Sources tab.");
  }
  if (!Array.isArray(sourceFiles) || sourceFiles.length !== 2
      || new Set(sourceFiles.map(source => source?.name)).size !== 2
      || !sourceFiles.every(source => ["AGENTS.md", "SKILL.md"].includes(source?.name)
        && typeof source.content === "string" && source.content.trim())) {
    throw sourceError("INVALID_REQUEST",
      "Project sources must be non-blank AGENTS.md and SKILL.md files.");
  }
  const names = sourceFiles.map(source => source.name);
  const deleted = await deleteExistingSources(names, log);
  try {
    await uploadSources(sourceFiles, timeoutMs, log);
  } catch (error) {
    if (deleted.length) {
      error.message = "Removed existing " + [...new Set(deleted)].join(", ") +
        ", but replacement upload was not confirmed. " + error.message;
    }
    throw error;
  }
}

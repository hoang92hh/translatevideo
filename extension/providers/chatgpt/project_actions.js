/** Create a ChatGPT project from the dedicated /projects page. */
const visible = node => !!node && node.isConnected && node.getClientRects().length > 0;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const failed = message => Object.assign(new Error(message), { code: "PROJECT_CREATE_FAILED" });
const instructionsFailed = message =>
  Object.assign(new Error(message), { code: "PROJECT_INSTRUCTIONS_FAILED" });
const normalize = text => text.replace(/\r\n?/g, "\n").trim();

function labels(node) {
  return [node.innerText, node.getAttribute?.("aria-label"), node.getAttribute?.("title"),
    node.getAttribute?.("data-testid"), node.getAttribute?.("placeholder")]
    .filter(Boolean).map(value => value.trim().replace(/^\+\s*/, "").replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ").trim());
}

function hasLabel(node, pattern) {
  return labels(node).some(value => pattern.test(value));
}

function enabled(node) {
  return visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true";
}

async function waitFor(find, message, timeout = 12000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const found = find();
    if (found) return found;
    await pause(150);
  }
  throw failed(message);
}

async function waitForInstructions(find, message, timeout = 12000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const found = find();
    if (found) return found;
    await pause(150);
  }
  throw instructionsFailed(message);
}

async function waitForOptional(find, timeout = 1500) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const found = find();
    if (found) return found;
    await pause(150);
  }
  return null;
}

function createPageButton() {
  if (!/^\/projects\/?$/.test(location.pathname)) return null;
  const createLabel = /^(create|create project|t\u1ea1o|t\u1ea1o d\u1ef1 \u00e1n)$/i;
  const selectors = [
    'main [data-chatgpt-project-conversation-drop-target] > button[data-color="primary"][data-variant="solid"][data-size="xl"][data-pill]',
    '[data-chatgpt-project-conversation-drop-target] > button[data-color="primary"][data-variant="solid"][data-size="xl"][data-pill]',
    'main [data-chatgpt-project-conversation-drop-target] > button',
  ];
  for (const selector of selectors) {
    const candidates = [...document.querySelectorAll(selector)]
      .filter(enabled).filter(node => hasLabel(node, createLabel));
    if (candidates.length === 1) return candidates[0];
  }
  const buttons = [...document.querySelectorAll('button, [role="button"]')]
    .filter(enabled).filter(node => hasLabel(node, createLabel));
  return buttons.length === 1 ? buttons[0] : null;
}

function editableInputs(root = document) {
  return [...root.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea')]
    .filter(enabled).filter(node => !node.readOnly);
}

function projectEditor(inputsBeforeClick) {
  const namedInputs = [...document.querySelectorAll(
    '#chatgpt-project-name, input[name="project-name"]',
  )].filter(enabled).filter(node => !node.readOnly);
  if (namedInputs.length > 1) {
    throw failed("ChatGPT exposed more than one project name field.");
  }
  if (namedInputs.length === 1) {
    const input = namedInputs[0];
    const scope = input.closest('form, [role="dialog"], dialog, [data-radix-dialog-content], [data-state="open"]');
    if (scope) return { input, scope };
  }
  const overlays = [...document.querySelectorAll(
    '[role="dialog"], dialog, [data-radix-dialog-content], [data-state="open"]',
  )].filter(visible);
  for (const scope of overlays) {
    const candidates = editableInputs(scope).filter(node =>
      !hasLabel(node, /^search projects$|t\u00ecm ki\u1ebfm d\u1ef1 \u00e1n/i));
    const input = candidates.find(node => hasLabel(node,
      /project.*name|name.*project|t\u00ean.*d\u1ef1 \u00e1n|d\u1ef1 \u00e1n.*t\u00ean/i))
      || candidates.find(node => !inputsBeforeClick.has(node));
    if (input) return { input, scope };
  }
  // Some ChatGPT builds render a sheet without role=dialog. It must still be a newly-created input.
  const input = editableInputs().find(node => !inputsBeforeClick.has(node)
    && !hasLabel(node, /^search projects$|t\u00ecm ki\u1ebfm d\u1ef1 \u00e1n/i));
  if (!input) return null;
  const scope = input.closest('form, [role="dialog"], dialog, [data-radix-dialog-content], [data-state="open"]');
  return scope ? { input, scope } : null;
}

function submitButton(scope) {
  const buttons = [...scope.querySelectorAll('button, [role="button"]')].filter(enabled);
  return buttons.find(node => node.matches('button[type="submit"]')
    && hasLabel(node, /^(create|create project|t\u1ea1o|t\u1ea1o d\u1ef1 \u00e1n)$/i))
    || buttons.find(node => hasLabel(node,
      /^(create|create project|t\u1ea1o|t\u1ea1o d\u1ef1 \u00e1n)$/i));
}

function setInput(input, value, fail = failed) {
  input.focus();
  const prototype = input instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  if (!setter) throw fail("ChatGPT field cannot be edited.");
  setter.call(input, value);
  input.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

export async function createProject(name) {
  if (typeof name !== "string" || !name.trim()) throw failed("Project name is required.");
  if (!/^\/projects\/?$/.test(location.pathname)) {
    throw failed("ChatGPT is not on the Projects page.");
  }
  const cleanName = name.trim();
  const inputsBeforeClick = new Set(editableInputs());
  const trigger = await waitFor(createPageButton,
    "The Create button was not found on the ChatGPT Projects page.");
  trigger.click();
  const editor = await waitFor(() => projectEditor(inputsBeforeClick),
    "The project creation window did not appear.");
  setInput(editor.input, cleanName);
  if (editor.input.value !== cleanName) throw failed("ChatGPT did not accept the project name.");
  const submit = await waitFor(() => submitButton(editor.scope),
    "The Create button in the project creation window did not become available.");
  submit.click();
}

function projectActionsButton() {
  if (!/^\/g\/g-p-[^/]+\/project\/?$/.test(location.pathname)) return null;
  const candidates = [...document.querySelectorAll(
    'button[aria-label="Project actions"][aria-haspopup="menu"]',
  )].filter(enabled);
  if (candidates.length > 1) {
    throw instructionsFailed("ChatGPT exposed more than one Project actions button.");
  }
  return candidates[0] || null;
}

function projectSettingsItem() {
  const openMenus = [...document.querySelectorAll('[role="menu"][data-state="open"], [role="menu"]')]
    .filter(visible);
  const candidates = openMenus.flatMap(menu => [...menu.querySelectorAll('[role="menuitem"]')])
    .filter(enabled)
    .filter(node => hasLabel(node, /^(project settings|cài đặt dự án)$/i));
  if (candidates.length > 1) {
    throw instructionsFailed("ChatGPT exposed more than one Project settings menu item.");
  }
  return candidates[0] || null;
}

function settingsForm() {
  const forms = [...document.querySelectorAll("form")].filter(visible).filter(form => {
    const heading = form.querySelector("h1, h2, h3, [role=heading]");
    return heading && /^(project settings|cài đặt dự án)$/i.test(heading.innerText.trim());
  });
  if (forms.length > 1) {
    throw instructionsFailed("ChatGPT exposed more than one Project settings form.");
  }
  return forms[0] || null;
}

function projectActionsMenuOpen(trigger) {
  return trigger?.getAttribute("aria-expanded") === "true"
    || trigger?.getAttribute("data-state") === "open";
}

async function openVisibleProjectSettingsItem(item) {
  item.click();
  return waitForInstructions(settingsForm, "The Project settings form did not appear.");
}

async function openProjectSettings() {
  const existingForm = settingsForm();
  if (existingForm) return existingForm;

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const existingItem = projectSettingsItem();
    if (existingItem) return openVisibleProjectSettingsItem(existingItem);

    let trigger = await waitForInstructions(projectActionsButton,
      "The Project actions button was not found.", 15000);

    // Saving the form can leave the Radix trigger marked as open briefly. Clicking it
    // immediately would close the menu and make the settings item appear to be missing.
    if (projectActionsMenuOpen(trigger)) {
      const pendingItem = await waitForOptional(projectSettingsItem);
      if (pendingItem) return openVisibleProjectSettingsItem(pendingItem);

      trigger.click();
      await waitForOptional(() => {
        const current = projectActionsButton();
        return current && !projectActionsMenuOpen(current) ? current : null;
      }, 2500);
      trigger = await waitForInstructions(projectActionsButton,
        "The Project actions button was not found.");
    }

    trigger.click();
    const item = await waitForOptional(projectSettingsItem, 6000);
    if (item) return openVisibleProjectSettingsItem(item);
  }

  throw instructionsFailed("The Project settings menu item did not appear.");
}

function instructionsEditor(form) {
  const candidates = [...form.querySelectorAll('textarea[name="project-instructions"]')]
    .filter(enabled).filter(node => !node.readOnly);
  if (candidates.length > 1) {
    throw instructionsFailed("ChatGPT exposed more than one Project instructions field.");
  }
  return candidates[0] || null;
}

function settingsButton(form, pattern, submit = false) {
  const selector = submit ? 'button[type="submit"]' : 'button, [role="button"]';
  const candidates = [...form.querySelectorAll(selector)].filter(enabled)
    .filter(node => hasLabel(node, pattern));
  if (candidates.length > 1) {
    throw instructionsFailed("ChatGPT exposed more than one matching Project settings action.");
  }
  return candidates[0] || null;
}

async function closeVerifiedSettings(form) {
  const cancel = await waitForInstructions(() => settingsButton(form, /^(cancel|hủy)$/i),
    "The Cancel button in Project settings was not found.");
  cancel.click();
  await waitForInstructions(() => !form.isConnected || !visible(form),
    "The verified Project settings form did not close.");
}

export async function updateProjectInstructions(content) {
  if (typeof content !== "string" || !content.trim()) {
    throw instructionsFailed("Project instructions must be non-blank.");
  }
  if (!/^\/g\/g-p-[^/]+\/project\/?$/.test(location.pathname)) {
    throw instructionsFailed("ChatGPT is not on the selected project page.");
  }

  const form = await openProjectSettings();
  const editor = await waitForInstructions(() => instructionsEditor(form),
    "The Project instructions field was not found.");
  setInput(editor, content, instructionsFailed);
  if (normalize(editor.value) !== normalize(content)) {
    throw instructionsFailed("ChatGPT did not accept the complete Project instructions.");
  }
  const save = await waitForInstructions(() => settingsButton(form, /^(save|lưu)$/i, true),
    "The Save button in Project settings did not become available.");
  save.click();
  await waitForInstructions(() => !form.isConnected || !visible(form),
    "Project settings did not close after Save.", 15000);

  const verificationForm = await openProjectSettings();
  const verificationEditor = await waitForInstructions(() => instructionsEditor(verificationForm),
    "The Project instructions field was not found during verification.");
  if (normalize(verificationEditor.value) !== normalize(content)) {
    throw instructionsFailed("Saved Project instructions do not match the Markdown file.");
  }
  await closeVerifiedSettings(verificationForm);
}

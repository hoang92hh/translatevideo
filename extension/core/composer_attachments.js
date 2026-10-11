const attachmentError = (code, message) => Object.assign(new Error(message), { code });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function usableFileInput(input) {
  if (!input?.isConnected || input.disabled) return false;
  const accept = (input.getAttribute("accept") || "").toLowerCase();
  return !accept || accept.includes("json") || accept.includes("text") || accept.includes("*/*");
}

function findFileInput(doc, composer, preferredInput = null) {
  if (preferredInput) {
    if (!usableFileInput(preferredInput)) {
      throw attachmentError(
        "ATTACHMENT_INPUT_NOT_FOUND",
        "The selected composer file input is not available.",
      );
    }
    return preferredInput;
  }
  const composerScope = composer.closest("form, fieldset");
  const local = composerScope
    ? [...composerScope.querySelectorAll('input[type="file"]')].filter(usableFileInput)
    : [];
  const candidates = local.length
    ? local
    : [...doc.querySelectorAll('input[type="file"]')].filter(usableFileInput);
  if (candidates.length !== 1) {
    throw attachmentError(
      "ATTACHMENT_INPUT_NOT_FOUND",
      candidates.length
        ? "More than one composer file input was found; attachment was not attempted."
        : "The composer file input was not found.",
    );
  }
  return candidates[0];
}

function visible(win, node) {
  return !!node && node.isConnected && node.getClientRects().length > 0
    && win.getComputedStyle(node).visibility !== "hidden";
}

function attachmentVisible(doc, composer, name) {
  const composerScope = composer.closest("form, fieldset") || doc.body;
  const candidates = [...composerScope.querySelectorAll(
    '[data-testid*="attach" i], [data-testid*="file" i], [aria-label], [title], button, span, div',
  )];
  return candidates.some(node => {
    if (node === composer || node.contains(composer) || composer.contains(node)) return false;
    const label = [node.getAttribute("aria-label"), node.getAttribute("title"), node.textContent]
      .filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
    if (!label.includes(name) || !visible(doc.defaultView, node)) return false;
    const container = node.closest('[aria-busy], [data-testid*="attach" i], [data-testid*="file" i]') || node;
    return container.getAttribute("aria-busy") !== "true"
      && !/(uploading|processing|đang tải|đang xử lý)/i.test(label);
  });
}

export async function attachComposerFiles(doc, composer, attachments, {
  timeoutMs = 30000,
  log = () => {},
  fileInput = null,
} = {}) {
  if (!Array.isArray(attachments) || attachments.length !== 1) {
    throw attachmentError("INVALID_REQUEST", "Exactly one composer attachment is required.");
  }
  const attachment = attachments[0];
  if (typeof attachment?.name !== "string" || !attachment.name.trim()
      || typeof attachment.content !== "string" || !attachment.content
      || typeof attachment.mime_type !== "string" || !attachment.mime_type.trim()) {
    throw attachmentError("INVALID_REQUEST", "Attachment name, content and MIME type are required.");
  }
  const input = findFileInput(doc, composer, fileInput);
  const transfer = new doc.defaultView.DataTransfer();
  transfer.items.add(new doc.defaultView.File([attachment.content], attachment.name, {
    type: attachment.mime_type,
    lastModified: Date.now(),
  }));
  const setter = Object.getOwnPropertyDescriptor(
    doc.defaultView.HTMLInputElement.prototype, "files",
  )?.set;
  if (!setter) {
    throw attachmentError("ATTACHMENT_UPLOAD_FAILED", "The browser cannot assign composer files.");
  }
  setter.call(input, transfer.files);
  if (input.files?.length !== 1 || input.files[0]?.name !== attachment.name) {
    throw attachmentError("ATTACHMENT_UPLOAD_FAILED", "The composer did not receive the attachment.");
  }
  input.dispatchEvent(new doc.defaultView.Event("input", { bubbles: true }));
  input.dispatchEvent(new doc.defaultView.Event("change", { bubbles: true }));
  log("ATTACHMENT UPLOAD STARTED " + attachment.name);

  const deadline = Date.now() + timeoutMs;
  let stableChecks = 0;
  while (Date.now() < deadline) {
    const currentComposer = composer.isConnected ? composer : doc.querySelector('[contenteditable="true"], textarea');
    if (currentComposer && attachmentVisible(doc, currentComposer, attachment.name)) {
      if (++stableChecks >= 3) {
        log("ATTACHMENT READY " + attachment.name);
        return;
      }
    } else {
      stableChecks = 0;
    }
    await pause(250);
  }
  throw attachmentError(
    "ATTACHMENT_UPLOAD_TIMEOUT",
    `Attachment ${attachment.name} was not confirmed; prompt was not sent.`,
  );
}

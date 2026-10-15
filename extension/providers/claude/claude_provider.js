import { BaseProvider } from "../base_provider.js";
import { claudeSelectors as S } from "./claude_selectors.js";
import { attachComposerFiles } from "../../core/composer_attachments.js";
import { hasResponseFile, readResponseFile } from "../../core/response_files.js";

const fail = (code, message) => Object.assign(new Error(message), { code });
const normalize = value => (value || "").replace(/\r\n?/g, "\n").trim();
const comparable = value => normalize(value)
  .replace(/\u00a0/g, " ")
  .replace(/[\u200b-\u200d\ufeff]/gu, "")
  .replace(/\s+/gu, " ");
const RESPONSE_STABLE_MS = 4000;
const RESPONSE_POLL_MS = 250;
const SEND_READY_POLL_MS = 100;
const SEND_READY_TIMEOUT_MS = 60000;

export class ClaudeProvider extends BaseProvider {
  constructor({ document: doc = document, timeoutMs = 180000, log = () => {} } = {}) {
    super();
    this.doc = doc;
    this.win = doc.defaultView;
    this.timeoutMs = timeoutMs;
    this.log = log;
    this.sent = false;
    this.finished = false;
    this.cancelled = false;
    this.text = "";
  }

  visible(node) {
    return !!node && node.isConnected && node.getClientRects().length > 0
      && this.win.getComputedStyle(node).visibility !== "hidden";
  }

  find(selector, root = this.doc) {
    return [...root.querySelectorAll(selector)].find(node => this.visible(node)) || null;
  }

  editable(node) {
    return this.visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true"
      && (node.isContentEditable || node.tagName === "TEXTAREA");
  }

  composerText(node) {
    return node?.tagName === "TEXTAREA" ? node.value : node?.innerText || "";
  }

  composerRoot(composer) {
    return composer?.closest(S.composerRoot) || null;
  }

  activeComposer() {
    const candidates = [...this.doc.querySelectorAll(S.composer)].filter(node =>
      this.editable(node) && !node.closest("[inert], [hidden]"));
    return candidates.filter(node =>
      this.composerRoot(node)?.getAttribute("data-form") === "reply").at(-1)
      || candidates.at(-1)
      || null;
  }

  composerFileInput(composer) {
    const root = this.composerRoot(composer);
    for (let scope = root?.parentElement; scope && scope !== this.doc.body;
      scope = scope.parentElement) {
      const candidates = [...scope.querySelectorAll(S.fileInput)].filter(node =>
        node.isConnected && !node.disabled && !node.closest("[inert], [hidden]"));
      if (candidates.length === 1) return candidates[0];
    }
    return null;
  }

  sendButtons(composer) {
    const root = this.composerRoot(composer);
    return root ? [...root.querySelectorAll(S.send)] : [];
  }

  enabledSendButton(composer) {
    const candidates = this.sendButtons(composer).filter(node =>
      node.isConnected
      && !node.disabled
      && node.getAttribute("aria-disabled") !== "true"
      && !node.closest("[inert], [hidden]"));
    return candidates.length === 1 ? candidates[0] : null;
  }

  attachmentsReady(composer) {
    if (!this.attachmentNames?.length) return true;
    const root = this.composerRoot(composer);
    if (!root) return false;
    const thumbnails = [...root.querySelectorAll(S.attachment)].filter(node => this.visible(node));
    return this.attachmentNames.every(name => thumbnails.some(node => {
      const label = [node.getAttribute("title"), node.textContent]
        .filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
      return label.includes(name) && node.getAttribute("aria-busy") !== "true";
    }));
  }

  sendState(composer) {
    const root = this.composerRoot(composer);
    const buttons = root ? [...root.querySelectorAll(S.send)] : [];
    return JSON.stringify({
      composer_found: Boolean(composer),
      composer_root_found: Boolean(root),
      prompt_matches: Boolean(
        composer && this.prompt
        && comparable(this.composerText(composer)) === comparable(this.prompt)
      ),
      attachments_ready: this.attachmentsReady(composer),
      send_button_count: buttons.length,
      send_buttons: buttons.map(node => ({
        visible: this.visible(node),
        disabled: Boolean(node.disabled),
        aria_disabled: node.getAttribute("aria-disabled"),
        inside_inert: Boolean(node.closest("[inert]")),
      })),
    });
  }

  assistantTurns() {
    return [...this.doc.querySelectorAll(S.assistantTurn)].filter(node => this.visible(node));
  }

  async isReady() {
    if (this.find(S.signedOut)) return false;
    const composer = this.activeComposer();
    if (!composer) throw fail("COMPOSER_NOT_FOUND", "Claude composer was not found.");
    return this.editable(composer) && !this.find(S.stop);
  }

  async selectModel(_model) {
    return { model: "current", changed: false };
  }

  async setPrompt(content, options = {}) {
    if (this.sent) throw fail("DUPLICATE_EXECUTION", "This provider already attempted to send.");
    if (typeof content !== "string" || !content.trim()) {
      throw fail("INVALID_REQUEST", "Prompt must be a non-blank string.");
    }
    if (Object.keys(options).some(key => !["attachments", "response_file_name"].includes(key))) {
      throw fail("UNSUPPORTED_OPTION", "Only composer attachments and response files are supported.");
    }
    this.responseFileName = String(options.response_file_name || "").trim();
    this.attachmentNames = Array.isArray(options.attachments)
      ? options.attachments.map(item => String(item?.name || "").trim()).filter(Boolean)
      : [];
    if (!(await this.isReady())) throw fail("PROVIDER_NOT_READY", "Claude is not ready or is generating.");
    let composer = this.activeComposer();
    if (normalize(this.composerText(composer))) {
      throw fail("COMPOSER_NOT_EMPTY", "Clear the existing Claude draft before starting a job.");
    }
    if (options.attachments?.length) {
      const fileInput = this.composerFileInput(composer);
      if (!fileInput) {
        throw fail("ATTACHMENT_INPUT_NOT_FOUND", "Claude file input for the active composer was not found.");
      }
      await attachComposerFiles(this.doc, composer, options.attachments, {
        log: this.log,
        fileInput,
      });
      composer = this.activeComposer();
      if (!composer || !this.editable(composer)) {
        throw fail("COMPOSER_NOT_FOUND", "Claude composer disappeared after attachment upload.");
      }
    }
    this.prompt = content;
    composer.focus();
    if (composer.tagName === "TEXTAREA") {
      const setter = Object.getOwnPropertyDescriptor(this.win.HTMLTextAreaElement.prototype, "value").set;
      setter.call(composer, content);
    } else {
      const selection = this.win.getSelection();
      const range = this.doc.createRange();
      range.selectNodeContents(composer);
      selection.removeAllRanges();
      selection.addRange(range);
      if (!this.doc.execCommand("insertText", false, content)) {
        throw fail("COMPOSER_WRITE_FAILED", "Claude editor did not accept text insertion.");
      }
    }
    composer.dispatchEvent(new this.win.InputEvent("input", {
      bubbles: true, inputType: "insertText", data: content,
    }));
    await this.observe(() => {
      const current = this.activeComposer();
      return current && comparable(this.composerText(current)) === comparable(content) ? true : null;
    }, 5000, () => fail(
      "COMPOSER_WRITE_FAILED", "Claude composer did not stabilize with the complete prompt.",
    ));
  }

  observe(check, timeoutMs, timeoutError) {
    return new Promise((resolve, reject) => {
      let done = false, observer, timer;
      const cleanup = () => {
        done = true;
        observer?.disconnect();
        this.win.clearTimeout(timer);
        if (this.abortWait === abort) this.abortWait = null;
      };
      const abort = () => { cleanup(); reject(fail("PROVIDER_ERROR", "Local response wait cancelled.")); };
      const evaluate = () => {
        if (done) return;
        try {
          if (this.cancelled) return abort();
          const value = check();
          if (value) { cleanup(); resolve(value); }
        } catch (error) { cleanup(); reject(error); }
      };
      observer = new this.win.MutationObserver(evaluate);
      observer.observe(this.doc.documentElement, {
        subtree: true, childList: true, characterData: true, attributes: true,
      });
      timer = this.win.setTimeout(() => { cleanup(); reject(timeoutError()); }, Math.max(1, timeoutMs));
      this.abortWait = abort;
      evaluate();
    });
  }

  async sendPrompt() {
    if (this.sent) throw fail("DUPLICATE_EXECUTION", "sendPrompt may only be attempted once.");
    if (!this.prompt) throw fail("PROMPT_SEND_FAILED", "No prompt has been prepared.");
    const sendDeadline = Date.now() + SEND_READY_TIMEOUT_MS;
    let button = null;
    while (Date.now() < sendDeadline) {
      if (this.cancelled) throw fail("PROVIDER_ERROR", "Local send wait cancelled.");
      const current = this.activeComposer();
      const promptMatches = current
        && comparable(this.composerText(current)) === comparable(this.prompt);
      if (promptMatches && this.attachmentsReady(current)) {
        button = this.enabledSendButton(current);
      }
      if (button) break;
      await new Promise(resolve => this.win.setTimeout(resolve, SEND_READY_POLL_MS));
    }
    if (!button) {
      const current = this.activeComposer();
      throw fail(
        "PROMPT_SEND_FAILED",
        `No enabled Claude Send button was found after ${SEND_READY_TIMEOUT_MS / 1000}s. `
          + this.sendState(current),
      );
    }
    const composer = this.activeComposer();
    const currentButton = this.enabledSendButton(composer);
    if (!composer || comparable(this.composerText(composer)) !== comparable(this.prompt)
        || !this.attachmentsReady(composer) || !currentButton || this.find(S.stop)) {
      throw fail("PROMPT_SEND_FAILED", "Claude composer changed or generation is already active.");
    }
    this.beforeAssistantKeys = new Set(this.assistantTurns().map(
      node => this.messageKey(node),
    ));
    this.beforeActionControls = new Set(this.doc.querySelectorAll(S.turnActions));
    this.responseTurn = null;
    this.startedAt = Date.now();
    this.sent = true; // Set before the side effect; never click twice after an uncertain result.
    currentButton.click();
    await this.observe(() => {
      const current = this.activeComposer();
      return current && !normalize(this.composerText(current)) ? true : null;
    }, 10000, () => fail("PROMPT_SEND_FAILED", "Claude did not clear the composer after Send."));
    this.log("PROMPT SENT");
  }

  messageKey(node) {
    return node.getAttribute("data-turn-key")
      || node.closest(S.turn)?.getAttribute("data-index")
      || node;
  }

  currentAssistant() {
    if (this.responseTurn?.isConnected && this.visible(this.responseTurn)) return this.responseTurn;
    this.responseTurn = this.assistantTurns().filter(
      node => !this.beforeAssistantKeys.has(this.messageKey(node)),
    ).at(-1) || null;
    return this.responseTurn;
  }

  completionControls(turn) {
    const controls = [...this.doc.querySelectorAll(S.turnActions)].filter(node =>
      this.visible(node) && !this.beforeActionControls.has(node));
    return controls.find(node => {
      if (turn.contains(node)) return true;
      return Boolean(turn.compareDocumentPosition(node) & this.win.Node.DOCUMENT_POSITION_FOLLOWING);
    }) || null;
  }

  responseText(node) {
    if (!node) return "";
    const bodies = [...node.querySelectorAll(S.responseBody)].filter(
      body => !body.parentElement?.closest(S.responseBody) && this.visible(body),
    );
    const read = (element) => {
      if (element.nodeType === 3) return element.textContent;
      if (element.nodeType !== 1 || element.matches(S.nonResponse)) return "";
      if (element.tagName === "BR") return "\n";
      const text = [...element.childNodes].map(read).join("");
      return /^(P|DIV|PRE|LI|H[1-6]|BLOCKQUOTE|TR)$/.test(element.tagName) ? text + "\n" : text;
    };
    return bodies.map(read).map(normalize).filter(Boolean).join("\n\n").trim();
  }

  async waitForResponse() {
    if (!this.sent) throw fail("PROMPT_SEND_FAILED", "Prompt has not been sent.");
    this.log("WAITING RESPONSE");
    let sawAssistant = false;
    let stableValue = "";
    let stableSince = 0;
    let lastState = {};
    const deadline = this.startedAt + this.timeoutMs;
    while (Date.now() < deadline) {
      if (this.cancelled) throw fail("PROVIDER_ERROR", "Local response wait cancelled.");

      const assistant = this.currentAssistant();
      sawAssistant ||= Boolean(assistant);
      const controls = assistant ? this.completionControls(assistant) : null;
      const copyVisible = Boolean(controls && this.find(S.copyAction, controls));
      const responseTurn = assistant?.closest(S.turn) || assistant;
      const stopVisible = Boolean(this.find(S.stop));
      const streaming = Boolean(responseTurn && this.find(S.streaming, responseTurn));
      const composerEditable = this.editable(this.activeComposer());
      const value = assistant ? this.responseText(assistant) : "";
      const responseFile = this.responseFileName
        ? hasResponseFile(responseTurn, this.responseFileName) : false;
      const complete = copyVisible && !stopVisible && !streaming && composerEditable
        && Boolean(value || responseFile);
      const now = Date.now();

      lastState = {
        assistant_found: Boolean(assistant),
        response_length: value.length,
        copy_visible: copyVisible,
        stop_visible: stopVisible,
        streaming,
        composer_editable: composerEditable,
        stable_ms: stableSince ? now - stableSince : 0,
      };

      if (!complete) {
        stableValue = value;
        stableSince = 0;
      } else if (!stableSince || value !== stableValue) {
        stableValue = value;
        stableSince = now;
      } else if (now - stableSince >= RESPONSE_STABLE_MS) {
        // Re-read immediately before committing so a late DOM update cannot be
        // returned as the final response.
        const finalAssistant = this.currentAssistant();
        const finalControls = finalAssistant ? this.completionControls(finalAssistant) : null;
        const finalTurn = finalAssistant?.closest(S.turn) || finalAssistant;
        const finalValue = finalAssistant ? this.responseText(finalAssistant) : "";
        const finalComplete = finalAssistant === assistant
          && Boolean(finalControls && this.find(S.copyAction, finalControls))
          && !this.find(S.stop)
          && !(finalTurn && this.find(S.streaming, finalTurn))
          && this.editable(this.activeComposer());
        if (finalComplete && finalValue === stableValue) {
          this.text = finalValue;
          this.finished = true;
          this.log("RESPONSE COMPLETE");
          return;
        }
        stableValue = finalValue;
        stableSince = 0;
      }

      await new Promise(resolve => this.win.setTimeout(
        resolve,
        Math.min(RESPONSE_POLL_MS, Math.max(1, deadline - Date.now())),
      ));
    }
    throw fail(
      sawAssistant ? "RESPONSE_TIMEOUT" : "ASSISTANT_RESPONSE_NOT_FOUND",
      sawAssistant
        ? `Claude response did not remain complete and stable before the deadline. ${JSON.stringify(lastState)}`
        : "No Claude response for this prompt was found before the deadline.",
    );
  }

  async getResponse() {
    if (!this.finished) throw fail("PROVIDER_ERROR", "No confirmed Claude response is available.");
    if (this.responseFileName) {
      const assistant = this.currentAssistant();
      const responseRoot = assistant?.closest(S.turn) || assistant;
      const file = await readResponseFile(
        this.win, responseRoot, this.responseFileName, fail,
      );
      return { text: this.text, file };
    }
    return this.text;
  }

  async cancel() {
    this.cancelled = true;
    this.abortWait?.();
  }
}

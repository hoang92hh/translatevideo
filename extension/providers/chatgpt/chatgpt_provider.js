import { BaseProvider } from "../base_provider.js";
import { chatgptSelectors as S } from "./chatgpt_selectors.js";
import { readChatGPTModelState, selectChatGPTModel } from "./chatgpt_models.js";
import { attachComposerFiles } from "../../core/composer_attachments.js";
import { hasResponseFile, readResponseFile } from "../../core/response_files.js";

const error = (code, message) => Object.assign(new Error(message), { code });
const normalize = (text) => text.replace(/\r\n?/g, "\n").trim();
const comparable = text => normalize(text)
  .replace(/\u00a0/g, " ")
  .replace(/[\u200b-\u200d\ufeff]/gu, "")
  .replace(/\s+/gu, " ");
const RESPONSE_SETTLE_DELAY_MS = 2000;
const SEND_BUTTON_INITIAL_DELAY_MS = 12000;
const SEND_BUTTON_RETRY_DELAY_MS = 3000;
const SEND_BUTTON_MAX_ATTEMPTS = 3;
const RESPONSE_FILE_GRACE_MS = 120000;
const RESPONSE_FILE_POLL_MS = 250;

export class ChatGPTProvider extends BaseProvider {
  constructor({ document: doc = document, timeoutMs = 180000,
    log = () => {} } = {}) {
    super();
    this.doc = doc;
    this.win = doc.defaultView;
    this.timeoutMs = timeoutMs;
    this.log = log;
    this.sent = false;
    this.finished = false;
    this.cancelled = false;
    this.text = "";
    this.downloadedResponseFile = null;
  }

  visible(element) {
    return !!element && element.isConnected && element.getClientRects().length > 0
      && this.win.getComputedStyle(element).visibility !== "hidden";
  }

  find(selector, root = this.doc) {
    return [...root.querySelectorAll(selector)].find((element) => this.visible(element)) || null;
  }

  editable(element) {
    return this.visible(element) && !element.disabled
      && element.getAttribute("aria-disabled") !== "true"
      && (element.isContentEditable || element.tagName === "TEXTAREA");
  }

  composerText(element) {
    return element.tagName === "TEXTAREA" ? element.value : element.innerText;
  }

  composerRoot(composer) {
    return composer?.closest('[data-testid="composer"], [data-type="unified-composer"], form')
      || composer?.parentElement
      || null;
  }

  sendButtons(composer) {
    const root = this.composerRoot(composer);
    return root ? [...root.querySelectorAll(S.send)] : [];
  }

  enabledSendButton(composer) {
    const candidates = this.sendButtons(composer).filter(button =>
      this.visible(button)
      && !button.disabled
      && button.getAttribute("aria-disabled") !== "true"
      && !button.closest("[inert], [hidden]"));
    return candidates.length === 1 ? candidates[0] : null;
  }

  sendState(composer) {
    const root = this.composerRoot(composer);
    const buttons = this.sendButtons(composer);
    return JSON.stringify({
      composer_found: Boolean(composer),
      composer_root_found: Boolean(root),
      prompt_matches: Boolean(
        composer && this.prompt
        && comparable(this.composerText(composer)) === comparable(this.prompt)
      ),
      send_button_count: buttons.length,
      send_buttons: buttons.map(button => ({
        visible: this.visible(button),
        disabled: Boolean(button.disabled),
        aria_disabled: button.getAttribute("aria-disabled"),
        aria_label: button.getAttribute("aria-label"),
        data_testid: button.getAttribute("data-testid"),
        type: button.getAttribute("type"),
        role: button.getAttribute("role"),
        inside_inert: Boolean(button.closest("[inert]")),
      })),
    });
  }

  async isReady() {
    if (this.find(S.signedOut)) return false;
    const composer = this.find(S.composer);
    if (!composer) throw error("COMPOSER_NOT_FOUND", "ChatGPT composer was not found.");
    return this.editable(composer) && !this.find(S.stop);
  }

  async selectModel(model, effort) {
    return selectChatGPTModel(model, effort, { document: this.doc, log: this.log });
  }

  async getModelState() {
    return readChatGPTModelState({ document: this.doc, log: this.log });
  }

  async setPrompt(content, options = {}) {
    if (this.sent) throw error("DUPLICATE_EXECUTION", "This provider already attempted to send.");
    if (typeof content !== "string" || !content.trim()) {
      throw error("INVALID_REQUEST", "Prompt must be a non-blank string.");
    }
    if (Object.keys(options).some(key => !["attachments", "response_file_name"].includes(key))) {
      throw error("UNSUPPORTED_OPTION", "Only composer attachments and response files are supported.");
    }
    this.responseFileName = String(options.response_file_name || "").trim();
    if (!(await this.isReady())) throw error("PROVIDER_NOT_READY", "ChatGPT is not ready or is generating.");
    let composer = this.find(S.composer);
    if (normalize(this.composerText(composer))) {
      throw error("COMPOSER_NOT_EMPTY", "Clear the existing draft before starting a job.");
    }
    if (options.attachments?.length) {
      await attachComposerFiles(this.doc, composer, options.attachments, { log: this.log });
      composer = this.find(S.composer);
      if (!composer || !this.editable(composer)) {
        throw error("COMPOSER_NOT_FOUND", "ChatGPT composer disappeared after attachment upload.");
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
      // Native editing command updates the editor's input state; no clipboard.
      if (!this.doc.execCommand("insertText", false, content)) {
        throw error("COMPOSER_WRITE_FAILED", "The editor did not accept text insertion.");
      }
    }
    composer.dispatchEvent(new this.win.InputEvent("input", {
      bubbles: true, inputType: "insertText", data: content,
    }));
    await this.observe(() => {
      const current = this.find(S.composer);
      return current && comparable(this.composerText(current)) === comparable(content) ? true : null;
    }, 5000, () => error(
      "COMPOSER_WRITE_FAILED", "Composer did not stabilize with the complete prompt.",
    ));
  }

  messageKey(node) {
    return node.getAttribute("data-chatgpt-search-message-ids")
      || node.getAttribute("data-chatgpt-search-unit-key")
      || node.getAttribute("data-content-search-unit-key")
      || node.getAttribute("data-message-id")
      || node.closest(S.turn)?.getAttribute("data-testid") || node;
  }

  assistantTurns() {
    return [...this.doc.querySelectorAll(S.assistantTurn)].filter(node => this.visible(node));
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
    // Only response markdown, excluding turn controls and thinking/status UI.
    const bodies = [...node.querySelectorAll(S.responseBody)].filter(
      (body) => !body.parentElement?.closest(S.responseBody) && this.visible(body),
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

  observe(check, timeoutMs, timeoutError) {
    return new Promise((resolve, reject) => {
      let done = false;
      let observer, deadline;
      const cleanup = () => {
        done = true;
        observer?.disconnect();
        this.win.clearTimeout(deadline);
        if (this.abortWait === abort) this.abortWait = null;
      };
      const abort = () => { cleanup(); reject(error("PROVIDER_ERROR", "Local response wait cancelled.")); };
      const evaluate = () => {
        if (done) return;
        try {
          if (this.cancelled) return abort();
          const value = check();
          if (value) { cleanup(); resolve(value); }
        } catch (cause) { cleanup(); reject(cause); }
      };
      observer = new this.win.MutationObserver(evaluate);
      observer.observe(this.doc.documentElement, {
        subtree: true, childList: true, characterData: true, attributes: true,
      });
      deadline = this.win.setTimeout(() => {
        if (done) return;
        cleanup();
        reject(timeoutError());
      }, Math.max(1, timeoutMs));
      this.abortWait = abort;
      evaluate();
    });
  }

  async sendPrompt() {
    if (this.sent) throw error("DUPLICATE_EXECUTION", "sendPrompt may only be attempted once.");
    if (!this.prompt) throw error("PROMPT_SEND_FAILED", "No prompt has been prepared.");
    this.log("WAITING FOR SEND BUTTON");
    await new Promise(resolve => this.win.setTimeout(resolve, SEND_BUTTON_INITIAL_DELAY_MS));
    let button = null;
    for (let attempt = 1; attempt <= SEND_BUTTON_MAX_ATTEMPTS; attempt++) {
      if (this.cancelled) throw error("PROVIDER_ERROR", "Local send wait cancelled.");
      const current = this.find(S.composer);
      if (!current || comparable(this.composerText(current)) !== comparable(this.prompt)
          || this.find(S.stop)) {
        throw error("PROMPT_SEND_FAILED", "Composer changed or generation is already active.");
      }
      button = this.enabledSendButton(current);
      if (button) {
        this.log(`SEND BUTTON READY ${attempt}/${SEND_BUTTON_MAX_ATTEMPTS}`);
        break;
      }
      this.log(`SEND BUTTON NOT READY ${attempt}/${SEND_BUTTON_MAX_ATTEMPTS}`);
      if (attempt < SEND_BUTTON_MAX_ATTEMPTS) {
        await new Promise(resolve => this.win.setTimeout(resolve, SEND_BUTTON_RETRY_DELAY_MS));
      }
    }
    if (!button) {
      throw error(
        "PROMPT_SEND_FAILED",
        `No unique enabled Send button after ${SEND_BUTTON_MAX_ATTEMPTS} checks. `
          + this.sendState(this.find(S.composer)),
      );
    }
    const composer = this.find(S.composer);
    if (!composer || comparable(this.composerText(composer)) !== comparable(this.prompt)
        || button !== this.enabledSendButton(composer) || this.find(S.stop)) {
      throw error("PROMPT_SEND_FAILED", "Composer changed or generation is already active.");
    }
    this.beforeAssistantKeys = new Set(this.assistantTurns().map(
      (node) => this.messageKey(node),
    ));
    this.beforeActionControls = new Set(this.doc.querySelectorAll(S.turnActions));
    this.responseTurn = null;
    this.startedAt = Date.now();
    this.sent = true; // Set BEFORE the side effect; never fall back to Enter or click twice.
    button.click();
    await this.observe(() => {
      const current = this.find(S.composer);
      return current && !normalize(this.composerText(current)) ? true : null;
    }, 10000, () => error(
      "PROMPT_SEND_FAILED", "Send was attempted but the composer did not clear. Do not retry automatically.",
    ));
    this.log("PROMPT SENT");
  }

  async waitForResponse() {
    if (!this.sent) throw error("PROMPT_SEND_FAILED", "Prompt has not been sent.");
    this.log("WAITING RESPONSE");
    if (this.responseFileName) return this.waitForResponseFile();
    let sawAssistant = false;
    let lastState = {};
    const deadline = this.startedAt + this.timeoutMs;
    while (Date.now() < deadline) {
      await this.observe(() => {
        const assistant = this.currentAssistant();
        if (!assistant) return null;
        sawAssistant = true;
        const responseTurn = assistant.closest(S.turn) || assistant;
        if (this.responseFileName) {
          const responseFile = hasResponseFile(responseTurn, this.responseFileName);
          const stillGenerating = this.find(S.stop)
            || this.find(S.streaming, responseTurn);
          const composerEditable = this.editable(this.find(S.composer));
          return responseFile && !stillGenerating && composerEditable ? true : null;
        }
        const controls = this.completionControls(assistant);
        return controls && this.find(S.copyAction, controls) ? true : null;
      }, Math.max(1, deadline - Date.now()), () => error(
        sawAssistant ? "RESPONSE_TIMEOUT" : "ASSISTANT_RESPONSE_NOT_FOUND",
        sawAssistant && this.responseFileName
          ? `ChatGPT did not finish preparing ${this.responseFileName} before the deadline.`
          : sawAssistant ? "ChatGPT did not show a Copy button before the deadline."
          : "No assistant message for this prompt was found before the deadline.",
      ));

      // The Copy action can appear shortly before the response DOM has finished rendering.
      // Wait once, then inspect completion state; do not continuously compare response text.
      await new Promise(resolve => this.win.setTimeout(resolve, RESPONSE_SETTLE_DELAY_MS));
      if (this.cancelled) throw error("PROVIDER_ERROR", "Local response wait cancelled.");

      const assistant = this.currentAssistant();
      const controls = assistant ? this.completionControls(assistant) : null;
      const copyVisible = controls && this.find(S.copyAction, controls);
      const responseTurn = assistant?.closest(S.turn) || assistant;
      const stillGenerating = this.find(S.stop)
        || (responseTurn && this.find(S.streaming, responseTurn));
      const value = assistant ? this.responseText(assistant) : "";
      const responseFile = this.responseFileName
        ? hasResponseFile(responseTurn, this.responseFileName) : false;
      const composerEditable = this.editable(this.find(S.composer));
      const contentReady = this.responseFileName ? responseFile : Boolean(value);
      lastState = {
        assistant_found: Boolean(assistant),
        response_length: value.length,
        copy_visible: Boolean(copyVisible),
        still_generating: Boolean(stillGenerating),
        composer_editable: composerEditable,
        response_file_required: Boolean(this.responseFileName),
        response_file_found: responseFile,
      };
      const completionReady = this.responseFileName ? responseFile : copyVisible;
      if (completionReady && !stillGenerating && contentReady && composerEditable) {
        this.text = value;
        if (this.responseFileName) {
          this.log("DOWNLOADING RESPONSE FILE");
          this.downloadedResponseFile = await readResponseFile(
            this.win, responseTurn, this.responseFileName, error,
          );
          this.log("RESPONSE FILE DOWNLOADED");
        }
        this.finished = true;
        this.log("RESPONSE COMPLETE");
        return;
      }
    }
    throw error(
      "RESPONSE_TIMEOUT",
      `ChatGPT response did not reach a completed state before the deadline. ${JSON.stringify(lastState)}`,
    );
  }

  async waitForResponseFile() {
    const deadline = this.startedAt + this.timeoutMs;
    let sawAssistant = false;
    let sawStop = false;
    let fileWaitStartedAt = null;
    let lastState = {};

    while (Date.now() < deadline) {
      if (this.cancelled) throw error("PROVIDER_ERROR", "Local response wait cancelled.");
      const assistant = this.currentAssistant();
      const responseTurn = assistant?.closest(S.turn) || assistant;
      const stopVisible = Boolean(this.find(S.stop));
      const streaming = Boolean(responseTurn && this.find(S.streaming, responseTurn));
      const composerEditable = this.editable(this.find(S.composer));
      const controls = assistant ? this.completionControls(assistant) : null;
      const copyVisible = Boolean(controls && this.find(S.copyAction, controls));
      const responseFile = Boolean(responseTurn
        && hasResponseFile(responseTurn, this.responseFileName));
      sawAssistant ||= Boolean(assistant);
      sawStop ||= stopVisible;
      lastState = {
        assistant_found: Boolean(assistant),
        stop_visible: stopVisible,
        streaming,
        composer_editable: composerEditable,
        copy_visible: copyVisible,
        response_file_found: responseFile,
      };

      if (assistant && responseFile && !stopVisible && !streaming && composerEditable) {
        await new Promise(resolve => this.win.setTimeout(resolve, RESPONSE_SETTLE_DELAY_MS));
        if (this.cancelled) throw error("PROVIDER_ERROR", "Local response wait cancelled.");
        const current = this.currentAssistant();
        const currentTurn = current?.closest(S.turn) || current;
        if (currentTurn && hasResponseFile(currentTurn, this.responseFileName)
            && !this.find(S.stop) && !this.find(S.streaming, currentTurn)
            && this.editable(this.find(S.composer))) {
          this.text = this.responseText(current);
          this.log("DOWNLOADING RESPONSE FILE");
          this.downloadedResponseFile = await readResponseFile(
            this.win, currentTurn, this.responseFileName, error,
          );
          this.log("RESPONSE FILE DOWNLOADED");
          this.finished = true;
          this.log("RESPONSE COMPLETE");
          return;
        }
      }

      // The Stop button may appear again if ChatGPT resumes generating.
      // If a very fast response hid Stop between polls, Copy confirms completion.
      const responseFinished = assistant && !stopVisible
        && composerEditable && (sawStop || copyVisible);
      if (responseFinished && !responseFile) {
        if (fileWaitStartedAt === null) {
          fileWaitStartedAt = Date.now();
          this.log(`WAITING FOR RESPONSE FILE ${this.responseFileName} (120s)`);
        } else if (Date.now() - fileWaitStartedAt >= RESPONSE_FILE_GRACE_MS) {
          throw error(
            "RESPONSE_FILE_NOT_FOUND",
            `ChatGPT finished responding, but ${this.responseFileName} did not appear within 120 seconds. `
              + JSON.stringify(lastState),
          );
        }
      } else if (stopVisible) {
        fileWaitStartedAt = null;
      }

      await new Promise(resolve => this.win.setTimeout(resolve,
        Math.min(RESPONSE_FILE_POLL_MS, Math.max(1, deadline - Date.now())),
      ));
    }
    throw error(
      sawAssistant ? "RESPONSE_TIMEOUT" : "ASSISTANT_RESPONSE_NOT_FOUND",
      sawAssistant
        ? `ChatGPT did not finish preparing ${this.responseFileName} before the deadline. `
          + JSON.stringify(lastState)
        : "No assistant message for this prompt was found before the deadline.",
    );
  }

  async getResponse() {
    if (!this.finished) throw error("PROVIDER_ERROR", "No confirmed completed response is available.");
    if (this.responseFileName) {
      if (!this.downloadedResponseFile) {
        throw error(
          "RESPONSE_FILE_DOWNLOAD_FAILED",
          `The response file ${this.responseFileName} was not downloaded.`,
        );
      }
      return { text: this.text, file: this.downloadedResponseFile };
    }
    const assistant = this.currentAssistant();
    const controls = assistant ? this.completionControls(assistant) : null;
    const copyButton = controls && this.find(S.copyAction, controls);
    if (!copyButton) throw error("COPY_ACTION_NOT_FOUND", "ChatGPT Copy action is no longer available.");
    if (!this.win.navigator.clipboard?.readText) {
      throw error("CLIPBOARD_UNAVAILABLE", "Clipboard read access is unavailable in the ChatGPT tab.");
    }
    let previous = "";
    try {
      previous = await this.win.navigator.clipboard.readText();
    } catch (cause) {
      throw error("CLIPBOARD_UNAVAILABLE", `Cannot read clipboard before Copy: ${cause}`);
    }
    copyButton.click();
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      let copied;
      try {
        copied = await this.win.navigator.clipboard.readText();
      } catch (cause) {
        throw error("CLIPBOARD_UNAVAILABLE", `Cannot read clipboard after Copy: ${cause}`);
      }
      if (copied.trim() && (copied !== previous || comparable(copied) === comparable(this.text))) {
        this.log("RESPONSE COPIED");
        return copied.trim();
      }
      await new Promise(resolve => this.win.setTimeout(resolve, 100));
    }
    throw error("RESPONSE_COPY_FAILED", "ChatGPT Copy action did not provide the completed response.");
  }

  async cancel() {
    this.cancelled = true;
    this.abortWait?.(); // Local cleanup only; no website Stop click or job cancel API.
  }
}

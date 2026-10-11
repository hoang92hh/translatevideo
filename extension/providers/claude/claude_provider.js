import { BaseProvider } from "../base_provider.js";
import { claudeSelectors as S } from "./claude_selectors.js";
import { attachComposerFiles } from "../../core/composer_attachments.js";

const fail = (code, message) => Object.assign(new Error(message), { code });
const normalize = value => (value || "").replace(/\r\n?/g, "\n").trim();
const comparable = value => normalize(value)
  .replace(/\u00a0/g, " ")
  .replace(/[\u200b-\u200d\ufeff]/gu, "")
  .replace(/\s+/gu, " ");

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

  assistantTurns() {
    return [...new Set([...this.doc.querySelectorAll(S.assistant)].filter(node => this.visible(node)))];
  }

  async isReady() {
    if (this.find(S.signedOut)) return false;
    const composer = this.find(S.composer);
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
    if (Object.keys(options).some(key => key !== "attachments")) {
      throw fail("UNSUPPORTED_OPTION", "Only composer attachments are supported.");
    }
    if (!(await this.isReady())) throw fail("PROVIDER_NOT_READY", "Claude is not ready or is generating.");
    let composer = this.find(S.composer);
    if (normalize(this.composerText(composer))) {
      throw fail("COMPOSER_NOT_EMPTY", "Clear the existing Claude draft before starting a job.");
    }
    if (options.attachments?.length) {
      await attachComposerFiles(this.doc, composer, options.attachments, { log: this.log });
      composer = this.find(S.composer);
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
      const current = this.find(S.composer);
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
    const button = await this.observe(() => {
      const candidate = this.find(S.send);
      return candidate && !candidate.disabled && candidate.getAttribute("aria-disabled") !== "true"
        ? candidate : null;
    }, 10000, () => fail("PROMPT_SEND_FAILED", "No enabled Claude Send button was found."));
    const composer = this.find(S.composer);
    if (!composer || comparable(this.composerText(composer)) !== comparable(this.prompt)) {
      throw fail("PROMPT_SEND_FAILED", "Claude composer changed before submission.");
    }
    this.beforeTurns = new Set(this.assistantTurns());
    this.startedAt = Date.now();
    this.sent = true;
    button.click();
    await this.observe(() => {
      const current = this.find(S.composer);
      return current && !normalize(this.composerText(current)) ? true : null;
    }, 10000, () => fail("PROMPT_SEND_FAILED", "Claude did not clear the composer after Send."));
    this.log("PROMPT SENT");
  }

  currentAssistant() {
    if (this.responseTurn?.isConnected && this.visible(this.responseTurn)) return this.responseTurn;
    this.responseTurn = this.assistantTurns().filter(node => !this.beforeTurns.has(node)).at(-1) || null;
    return this.responseTurn;
  }

  async waitForResponse() {
    if (!this.sent) throw fail("PROMPT_SEND_FAILED", "Prompt has not been sent.");
    this.log("WAITING RESPONSE");
    let sawAssistant = false, stableText = "", stableSince = 0;
    const poll = async () => {
      const deadline = this.startedAt + this.timeoutMs;
      while (Date.now() < deadline) {
        if (this.cancelled) throw fail("PROVIDER_ERROR", "Local response wait cancelled.");
        const assistant = this.currentAssistant();
        if (assistant) {
          sawAssistant = true;
          const text = normalize(assistant.innerText || assistant.textContent);
          if (text && text === stableText) {
            if (!this.find(S.stop) && this.editable(this.find(S.composer)) && Date.now() - stableSince >= 1500) {
              return text;
            }
          } else {
            stableText = text;
            stableSince = Date.now();
          }
        }
        await new Promise(resolve => this.win.setTimeout(resolve, 250));
      }
      throw fail(
        sawAssistant ? "RESPONSE_TIMEOUT" : "ASSISTANT_RESPONSE_NOT_FOUND",
        sawAssistant ? "Claude did not finish before the deadline." : "No Claude response was found.",
      );
    };
    this.text = await poll();
    this.finished = true;
    this.log("RESPONSE COMPLETE");
  }

  async getResponse() {
    if (!this.finished) throw fail("PROVIDER_ERROR", "No confirmed Claude response is available.");
    return this.text;
  }

  async cancel() {
    this.cancelled = true;
    this.abortWait?.();
  }
}

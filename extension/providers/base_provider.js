/** Contract skeleton. Normative semantics: docs/PROVIDER_SPEC.md.
 * No DOM, website defaults or shared website interaction logic belongs here.
 */
export class BaseProvider {
  async isReady() { throw new Error("NOT_IMPLEMENTED"); }
  async selectModel(model) { throw new Error("NOT_IMPLEMENTED"); }
  async setPrompt(content, options) { throw new Error("NOT_IMPLEMENTED"); }
  async sendPrompt() { throw new Error("NOT_IMPLEMENTED"); }
  async waitForResponse() { throw new Error("NOT_IMPLEMENTED"); }
  async getResponse() { throw new Error("NOT_IMPLEMENTED"); }
  async cancel() { throw new Error("NOT_IMPLEMENTED"); }
}

import { ChatGPTProvider } from "../providers/chatgpt/chatgpt_provider.js";
import { ClaudeProvider } from "../providers/claude/claude_provider.js";

/** Selection only: no DOM, tab lookup or prompt execution. */
const providers = new Map([
  ["chatgpt", ChatGPTProvider],
  ["claude", ClaudeProvider],
]);
export class ProviderFactory {
  create(providerId, context = {}) {
    const Provider = providers.get(providerId);
    if (!Provider) throw Object.assign(new Error("Provider execution is not implemented."), {
      code: "UNSUPPORTED_PROVIDER",
    });
    return new Provider(context);
  }
}

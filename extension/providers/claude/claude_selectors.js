export const claudeSelectors = Object.freeze({
  composer: [
    'div[contenteditable="true"][data-placeholder]',
    'div[contenteditable="true"].ProseMirror',
    'fieldset div[contenteditable="true"]',
    'textarea[placeholder]',
  ].join(","),
  send: [
    'button[aria-label*="Send"]',
    'button[data-testid*="send"]',
    'fieldset button[type="button"]:last-of-type',
  ].join(","),
  stop: [
    'button[aria-label*="Stop"]',
    'button[data-testid*="stop"]',
    '[data-is-streaming="true"]',
  ].join(","),
  assistant: [
    '[data-testid="assistant-message"]',
    '[data-testid*="assistant"] [data-testid*="message"]',
    '.font-claude-response',
    '[data-is-streaming]',
  ].join(","),
  signedOut: 'a[href*="login"], button[data-testid*="login"]',
});

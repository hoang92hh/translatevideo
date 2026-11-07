export const claudeSelectors = Object.freeze({
  composerRoot: '[data-cds="ChatComposer"]',
  composer: '[data-testid="chat-input"][contenteditable="true"]',
  fileInput: 'input[data-testid="file-upload"][type="file"]',
  send: [
    'button[data-testid="chat-input-send"]',
    '[data-cds-part="send-slot"] button[aria-label="Send message"]',
  ].join(","),
  attachment: '[data-testid="file-thumbnail"]',
  stop: [
    'button[aria-label*="Stop"]',
    'button[data-testid*="stop"]',
  ].join(","),
  assistantTurn: '[data-testid="assistant-message"][data-turn-key]',
  turn: '[data-testid="transcript-row"]',
  responseBody: '[data-perf-reply-text]',
  turnActions: '[data-testid="message-actions"]',
  copyAction: 'button[aria-label="Copy"]',
  streaming: '[data-is-streaming="true"], [data-perf-row-streaming="true"]',
  nonResponse: 'button, [role="button"], svg, style, script, [aria-hidden="true"]',
  signedOut: 'a[href*="login"], button[data-testid*="login"]',
});

/** Website routing metadata used by the background executor. */
export const claudeTarget = Object.freeze({
  matches: ["https://claude.ai/*"],
  origin: "https://claude.ai",
  home: "https://claude.ai/new",
  missingTabCode: "CLAUDE_TAB_NOT_FOUND",
  multipleTabsCode: "CLAUDE_MULTIPLE_TABS",
  foundLog: "CLAUDE TAB FOUND",
});

/** Website routing metadata, not DOM logic. Used by the background executor. */
export const chatgptTarget = Object.freeze({
  matches: ["https://chatgpt.com/*"],
  origin: "https://chatgpt.com",
  home: "https://chatgpt.com/",
  missingTabCode: "CHATGPT_TAB_NOT_FOUND",
  multipleTabsCode: "CHATGPT_MULTIPLE_TABS",
  foundLog: "CHATGPT TAB FOUND",
});

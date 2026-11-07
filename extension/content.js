/** Classic content-script entry point; provider modules run in the isolated world. */
void import(chrome.runtime.getURL("core/content_runner.js")).catch((error) => {
  console.error("[MyTool] Content script initialization failed:", error.message);
});

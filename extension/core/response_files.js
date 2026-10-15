const MAX_RESPONSE_FILE_BYTES = 8 * 1024 * 1024;
const RESPONSE_FILE_FETCH_TIMEOUT_MS = 30000;
const RESPONSE_FILE_DOWNLOAD_TIMEOUT_MS = 120000;

function normalizedName(value) {
  if (!value) return "";
  try {
    return decodeURIComponent(String(value)).trim();
  } catch {
    return String(value).trim();
  }
}

function candidateNames(link) {
  const values = [
    link.getAttribute("download"),
    link.getAttribute("data-file-name"),
    link.getAttribute("title"),
    link.getAttribute("aria-label"),
    link.textContent,
  ];
  const href = link.getAttribute("href") || "";
  try {
    const parsed = new URL(href, link.baseURI);
    values.push(parsed.pathname.split("/").at(-1));
    for (const key of ["filename", "file_name", "name"]) {
      values.push(parsed.searchParams.get(key));
    }
  } catch {
    values.push(href.split(/[?#]/u, 1)[0].split("/").at(-1));
  }
  return values.map(normalizedName).filter(Boolean);
}

export function findResponseFile(root, expectedName) {
  if (!root || !expectedName) return null;
  const expected = normalizedName(expectedName);
  const links = [...root.querySelectorAll("a[href]")];
  return links.find(link => candidateNames(link).some(name =>
    name === expected || name.endsWith("/" + expected)
  )) || null;
}

export function findResponseDownloadButton(root, expectedName) {
  if (!root || !expectedName) return null;
  const previewLabel = `Open preview of ${expectedName}`;
  const preview = [...root.querySelectorAll("button[aria-label]")].find(
    button => button.getAttribute("aria-label") === previewLabel,
  );
  if (!preview) return null;
  const row = preview.closest('[class~="group/resource-row"]') || preview.parentElement;
  if (!row) return null;
  const title = [...row.querySelectorAll("[title]")].find(
    node => node.getAttribute("title") === expectedName,
  );
  if (!title) return null;
  return [...row.querySelectorAll('button[aria-label="Download file"]')].find(
    button => !button.disabled && button.getAttribute("aria-disabled") !== "true",
  ) || null;
}

export function hasResponseFile(root, expectedName) {
  return Boolean(
    findResponseFile(root, expectedName)
    || findResponseDownloadButton(root, expectedName),
  );
}

async function readResponseLink(win, link, expectedName, makeError) {
  const href = link.href || link.getAttribute("href") || "";
  if (!href) {
    throw makeError("RESPONSE_FILE_DOWNLOAD_FAILED", `File ${expectedName} has no download URL.`);
  }
  let response;
  try {
    response = await win.fetch(href, {
      credentials: "include",
      cache: "no-store",
      signal: AbortSignal.timeout(RESPONSE_FILE_FETCH_TIMEOUT_MS),
    });
  } catch (cause) {
    throw makeError(
      "RESPONSE_FILE_DOWNLOAD_FAILED",
      `Could not download ${expectedName}: ${cause?.message || String(cause)}`,
    );
  }
  if (!response.ok) {
    throw makeError(
      "RESPONSE_FILE_DOWNLOAD_FAILED",
      `Could not download ${expectedName}: HTTP ${response.status}.`,
    );
  }
  const declaredLength = Number(response.headers.get("content-length") || 0);
  if (declaredLength > MAX_RESPONSE_FILE_BYTES) {
    throw makeError("RESPONSE_FILE_TOO_LARGE", `${expectedName} exceeds the 8 MB limit.`);
  }
  const content = await response.text();
  if (!content.trim()) {
    throw makeError("RESPONSE_FILE_DOWNLOAD_FAILED", `${expectedName} is empty.`);
  }
  if (new TextEncoder().encode(content).byteLength > MAX_RESPONSE_FILE_BYTES) {
    throw makeError("RESPONSE_FILE_TOO_LARGE", `${expectedName} exceeds the 8 MB limit.`);
  }
  return {
    name: expectedName,
    mime_type: response.headers.get("content-type") || "application/json",
    content,
  };
}

async function downloadResponseButton(win, button, expectedName, makeError) {
  let token = "";
  try {
    const prepared = await chrome.runtime.sendMessage({
      type: "MYTOOL_PREPARE_RESPONSE_DOWNLOAD",
      expected_name: expectedName,
    });
    if (!prepared?.ready || !prepared.token) {
      throw makeError(
        "RESPONSE_FILE_DOWNLOAD_FAILED",
        prepared?.message || `Could not prepare download tracking for ${expectedName}.`,
      );
    }
    token = prepared.token;
    button.click();
    const deadline = Date.now() + RESPONSE_FILE_DOWNLOAD_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (!button.isConnected) {
        // The resource card may rerender after the click; the registered download remains authoritative.
      }
      const status = await chrome.runtime.sendMessage({
        type: "MYTOOL_RESPONSE_DOWNLOAD_STATUS",
        token,
      });
      if (status?.status === "complete" && status.path) {
        token = "";
        return {
          name: expectedName,
          mime_type: "application/json",
          path: status.path,
        };
      }
      if (status?.status === "error") {
        throw makeError(
          "RESPONSE_FILE_DOWNLOAD_FAILED",
          status.message || `Chrome could not download ${expectedName}.`,
        );
      }
      await new Promise(resolve => win.setTimeout(resolve, 250));
    }
    throw makeError(
      "RESPONSE_FILE_DOWNLOAD_TIMEOUT",
      `Chrome did not finish downloading ${expectedName} within 120 seconds.`,
    );
  } finally {
    if (token) {
      await chrome.runtime.sendMessage({
        type: "MYTOOL_CANCEL_RESPONSE_DOWNLOAD",
        token,
      }).catch(() => null);
    }
  }
}

export async function readResponseFile(win, root, expectedName, makeError) {
  const link = findResponseFile(root, expectedName);
  if (link) return readResponseLink(win, link, expectedName, makeError);
  const button = findResponseDownloadButton(root, expectedName);
  if (button) return downloadResponseButton(win, button, expectedName, makeError);
  throw makeError(
    "RESPONSE_FILE_NOT_FOUND",
    `The assistant did not return the required file ${expectedName}.`,
  );
}

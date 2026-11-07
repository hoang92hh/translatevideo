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
  const expected = normalizedName(expectedName);
  const namedNodes = [...root.querySelectorAll(
    '[title], [data-file-name], [download], a[href]',
  )].filter(node => {
    if (node.tagName === "A") {
      return candidateNames(node).some(name =>
        name === expected || name.endsWith("/" + expected)
      );
    }
    return ["title", "data-file-name", "download"].some(
      attribute => normalizedName(node.getAttribute(attribute)) === expected,
    );
  });

  const enabled = node => !node.disabled
    && node.getAttribute("aria-disabled") !== "true";

  for (const namedNode of namedNodes) {
    if (namedNode.matches("a[download]") && enabled(namedNode)) return namedNode;

    const row = namedNode.closest('[class~="group/resource-row"]');
    if (!row || !root.contains(row)) continue;
    const preview = [...row.children].find(node =>
      node.matches('button, [role="button"]')
    );
    if (!preview) continue;
    const actions = [...row.querySelectorAll(
      'button, [role="button"], a[download]',
    )].filter(node =>
      node !== preview
      && enabled(node)
      && !node.contains(namedNode)
    );
    if (actions.length === 1) return actions[0];
  }
  return null;
}

function comparableArtifactName(value) {
  return normalizedName(value)
    .replace(/\.json$/iu, "")
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[\s_-]+/gu, "");
}

function labelName(node, prefix) {
  const label = normalizedName(node?.getAttribute("aria-label"));
  return label.startsWith(prefix) ? label.slice(prefix.length).trim() : "";
}

export function findClaudeArtifactDownloadButton(root, expectedName) {
  if (!root || !expectedName) return null;
  const expected = comparableArtifactName(expectedName);
  const cards = [...root.querySelectorAll('[data-sheet-kind="json"]')];
  for (const card of cards) {
    const open = card.querySelector(
      'button[data-testid="file-card-open"][aria-label^="View "]',
    );
    const download = card.querySelector('button[aria-label^="Download "]');
    if (!open || !download || download.disabled
        || download.getAttribute("aria-disabled") === "true") continue;
    const openName = comparableArtifactName(labelName(open, "View "));
    const downloadName = comparableArtifactName(labelName(download, "Download "));
    if (openName === expected && downloadName === expected) return download;
  }
  return null;
}

export function hasResponseFile(root, expectedName) {
  return Boolean(
    findResponseFile(root, expectedName)
    || findResponseDownloadButton(root, expectedName)
    || findClaudeArtifactDownloadButton(root, expectedName),
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
    button.focus({ preventScroll: true });
    await new Promise(resolve => win.requestAnimationFrame(() =>
      win.requestAnimationFrame(resolve)
    ));
    if (!button.isConnected || button.disabled
        || button.getAttribute("aria-disabled") === "true") {
      throw makeError(
        "RESPONSE_FILE_DOWNLOAD_FAILED",
        `The download action for ${expectedName} is no longer available.`,
      );
    }
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
  const button = findResponseDownloadButton(root, expectedName)
    || findClaudeArtifactDownloadButton(root, expectedName);
  if (button) return downloadResponseButton(win, button, expectedName, makeError);
  const link = findResponseFile(root, expectedName);
  if (link) return readResponseLink(win, link, expectedName, makeError);
  throw makeError(
    "RESPONSE_FILE_NOT_FOUND",
    `The assistant did not return the required file ${expectedName}.`,
  );
}

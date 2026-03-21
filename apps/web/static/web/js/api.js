import { getCookie } from "./utils.js";

async function parseJson(response) {
    try {
        return await response.json();
    } catch {
        return {};
    }
}

export async function requestGraphFromUrl(url, endpoint) {
    const response = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
        },
        body: JSON.stringify({ url }),
    });

    const payload = await parseJson(response);
    if (!response.ok) {
        throw new Error(
            payload.error || "ChaosWing could not load that market.",
        );
    }
    return payload;
}

/**
 * Fetch a paginated list of recent GraphRun records.
 *
 * @param {string} endpoint  - The runs list API URL (e.g. /api/v1/runs/).
 * @param {object} [options]
 * @param {number} [options.limit=20]   - Max runs to return (server caps at 50).
 * @param {number} [options.offset=0]   - Pagination offset.
 * @param {AbortSignal} [options.signal] - Optional AbortSignal for cancellation.
 *
 * @returns {Promise<{ runs: object[], total: number, limit: number, offset: number }>}
 */
export async function listGraphRuns(
    endpoint,
    { limit = 20, offset = 0, signal } = {},
) {
    const url = new URL(endpoint, window.location.origin);
    url.searchParams.set("limit", String(limit));
    url.searchParams.set("offset", String(offset));

    const response = await fetch(url.toString(), {
        method: "GET",
        credentials: "same-origin",
        headers: {
            Accept: "application/json",
        },
        signal,
    });

    const payload = await parseJson(response);
    if (!response.ok) {
        throw new Error(
            payload.error || "ChaosWing could not fetch run history.",
        );
    }
    return payload;
}

/**
 * Fetch the full payload for a single saved GraphRun by ID.
 *
 * @param {string} runDetailUrl  - The full detail URL (e.g. /api/v1/runs/<uuid>/).
 * @param {AbortSignal} [signal] - Optional AbortSignal.
 *
 * @returns {Promise<object>}  The full GraphRun payload as returned by the backend.
 */
export async function fetchGraphRun(runDetailUrl, signal) {
    const response = await fetch(runDetailUrl, {
        method: "GET",
        credentials: "same-origin",
        headers: {
            Accept: "application/json",
        },
        signal,
    });

    const payload = await parseJson(response);
    if (!response.ok) {
        throw new Error(payload.error || "ChaosWing could not load that run.");
    }
    return payload;
}

/**
 * Export / download a GraphRun payload as a JSON file in the browser.
 * No server round-trip required - the payload is already in memory.
 *
 * @param {object} payload   - The full graph payload returned by the backend.
 * @param {string} [filename] - Override the default filename.
 */
export function exportGraphPayload(payload, filename) {
    const slug =
        payload?.event?.title
            ?.toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "")
            .slice(0, 48) || "graph";

    const defaultFilename = `chaoswing-${slug}.json`;
    const name = filename || defaultFilename;

    const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
    });

    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();

    // Revoke after a brief delay to allow the download to start.
    setTimeout(() => URL.revokeObjectURL(url), 5000);
}

/**
 * Build a shareable permalink for a graph run.
 * Uses the run ID embedded in the payload to construct the detail URL pattern,
 * or falls back to a dashboard URL with the source URL as a query parameter.
 *
 * @param {object} payload - The full graph payload.
 * @param {string} dashboardUrl - The base dashboard URL (e.g. /app/).
 *
 * @returns {string} An absolute URL suitable for copying to clipboard.
 */
export function buildShareUrl(payload, dashboardUrl) {
    const briefUrl = payload?.run?.brief_url;
    if (briefUrl) {
        return new URL(briefUrl, window.location.origin).href;
    }

    const detailUrl = payload?.run?.detail_url;
    if (detailUrl) {
        return new URL(detailUrl, window.location.origin).href;
    }

    const sourceUrl = payload?.event?.source_url || "";
    if (sourceUrl) {
        const base = new URL(dashboardUrl, window.location.origin);
        base.searchParams.set("url", sourceUrl);
        return base.href;
    }

    return new URL(dashboardUrl, window.location.origin).href;
}


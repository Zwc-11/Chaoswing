import { getCookie } from "./utils.js";

async function parseJson(response) {
    try {
        return await response.json();
    } catch {
        return {};
    }
}

async function parseText(response) {
    try {
        return await response.text();
    } catch {
        return "";
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
        throw new Error(payload.error || "ChaosWing could not load that market.");
    }
    return payload;
}

export async function requestInspectorPartial(endpoint, options = {}) {
    const { method = "GET", payload = null, signal } = options;
    const response = await fetch(endpoint, {
        method,
        credentials: "same-origin",
        headers:
            method === "POST"
                ? {
                      "Content-Type": "application/json",
                      Accept: "text/html",
                      "X-CSRFToken": getCookie("csrftoken"),
                  }
                : { Accept: "text/html" },
        body: method === "POST" ? JSON.stringify(payload || {}) : undefined,
        signal,
    });

    const html = await parseText(response);
    if (!response.ok) {
        throw new Error(html || "The inspector partial could not be loaded.");
    }
    return html;
}

export function readInitialState(scriptId) {
    const element = document.getElementById(scriptId);
    if (!element) {
        return {};
    }

    try {
        return JSON.parse(element.textContent || "{}");
    } catch {
        return {};
    }
}

export function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
}

export function clearElement(element) {
    while (element.firstChild) {
        element.removeChild(element.firstChild);
    }
}

export function createTag(text, className = "tag") {
    const element = document.createElement("span");
    element.className = className;
    element.textContent = text;
    return element;
}

export function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (const cookie of cookies) {
        const trimmed = cookie.trim();
        if (trimmed.startsWith(`${name}=`)) {
            return decodeURIComponent(trimmed.slice(name.length + 1));
        }
    }
    return "";
}

export function formatDate(value) {
    if (!value) {
        return "Unknown";
    }

    return new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
    }).format(new Date(value));
}

export function formatCount(nodes, edges) {
    return `${nodes} nodes / ${edges} edges`;
}

export function humanizeRelationship(value) {
    return (value || "")
        .replaceAll("_", " ")
        .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function readCssVariable(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function isEditableTarget(target) {
    return Boolean(
        target &&
            (target.tagName === "INPUT" ||
                target.tagName === "TEXTAREA" ||
                target.tagName === "SELECT" ||
                target.isContentEditable)
    );
}

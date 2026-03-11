function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function humanizeType(value) {
    const mapping = {
        Event: "Event",
        Entity: "Entity",
        RelatedMarket: "Related market",
        Evidence: "Evidence",
        Rule: "Rule",
        Hypothesis: "Hypothesis",
    };
    return mapping[value] || value || "Unknown";
}

function humanizeRelationship(value) {
    return String(value || "")
        .split("_")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
}

function confidencePercent(value) {
    const confidence = Number(value);
    if (Number.isNaN(confidence)) {
        return 0;
    }
    return Math.max(0, Math.min(100, Math.round(confidence * 100)));
}

function formatPercent(value) {
    const numeric = Number(value);
    if (Number.isNaN(numeric)) {
        return escapeHtml(value);
    }
    return Number.isInteger(numeric) ? `${numeric}` : numeric.toFixed(1);
}

function buildBar(label, value, caption, tone = "accent") {
    return `
        <div class="bar-row">
            <div class="bar-row__header">
                <span class="bar-row__label">${escapeHtml(label)}</span>
                <span class="bar-row__value">${value}%</span>
            </div>
            <div class="bar-row__track" aria-hidden="true">
                <span class="bar-row__fill bar-row__fill--${tone}" style="--bar-value: ${value}%;"></span>
            </div>
            <p class="bar-row__caption">${escapeHtml(caption)}</p>
        </div>
    `;
}

function buildDetailItems(items) {
    if (!items?.length) {
        return "";
    }

    return `
        <div class="detail-list">
            ${items
                .map(
                    (item) => `
                        <div class="detail-item">
                            <span class="detail-item__label">${escapeHtml(item.label)}</span>
                            <span class="detail-item__value">${escapeHtml(item.value)}</span>
                        </div>
                    `
                )
                .join("")}
        </div>
    `;
}

function buildEvidence(snippets) {
    if (!snippets?.length) {
        return "";
    }

    return `
        <section class="inspector-section">
            <p class="subheading">Evidence</p>
            <ul class="evidence-list">
                ${snippets.map((snippet) => `<li>${escapeHtml(snippet)}</li>`).join("")}
            </ul>
        </section>
    `;
}

function renderEmpty() {
    return `
        <section class="inspector-panel inspector-panel--empty">
            <p class="inspector-kicker">Selection profile</p>
            <h3 class="inspector-title">Select a graph element</h3>
            <p class="inspector-copy">
                Hover for a quick preview. Click once to lock the graph while you read the detail.
            </p>

            <div class="step-list">
                <div class="step-card">
                    <span class="step-card__index">1</span>
                    <span class="step-card__label">Load a market</span>
                </div>
                <div class="step-card">
                    <span class="step-card__index">2</span>
                    <span class="step-card__label">Hover to preview a node</span>
                </div>
                <div class="step-card">
                    <span class="step-card__index">3</span>
                    <span class="step-card__label">Click to lock and read</span>
                </div>
            </div>
        </section>
    `;
}

function renderNode(selection) {
    const data = selection.data;
    const summary = data.summary ? `<p class="inspector-copy">${escapeHtml(data.summary)}</p>` : "";
    const hasProbability =
        data.probability !== null && data.probability !== undefined && data.probability !== "";
    const probabilityBlock = hasProbability
        ? `
            <section class="inspector-section inspector-section--odds">
                <p class="subheading">${escapeHtml(data.probability_label || "Implied chance")}</p>
                <p class="inspector-odds">${formatPercent(data.probability)}%</p>
            </section>
        `
        : "";
    const sourceBlock =
        data.icon_url || data.source_url || data.source_title || data.source_description
            ? `
                <section class="inspector-section inspector-section--source">
                    ${
                        data.icon_url
                            ? `<img class="inspector-preview" src="${escapeHtml(data.icon_url)}" alt="${escapeHtml(data.label)}">`
                            : ""
                    }
                    ${
                        data.source_title || data.source_description
                            ? `
                                <div class="inspector-source-copy">
                                    ${
                                        data.source_title
                                            ? `<p class="subheading">Source market</p><p class="inspector-source-title">${escapeHtml(data.source_title)}</p>`
                                            : ""
                                    }
                                    ${
                                        data.source_description
                                            ? `<p class="inspector-copy inspector-copy--tight">${escapeHtml(data.source_description)}</p>`
                                            : ""
                                    }
                                </div>
                            `
                            : ""
                    }
                    ${
                        data.source_url
                            ? `
                                <div class="inspector-url-block">
                                    <p class="subheading">Event link</p>
                                    <a class="inspector-url" href="${escapeHtml(data.source_url)}" rel="noreferrer" target="_blank">${escapeHtml(data.source_url)}</a>
                                </div>
                            `
                            : ""
                    }
                </section>
            `
            : "";
    const actionBlock = `
        <div class="inspector-actions">
            <button class="button button--ghost button--compact" type="button" data-action="center-node" data-node-id="${escapeHtml(data.id)}">
                Center on node
            </button>
            ${
                data.source_url
                    ? `<a class="button button--ghost button--compact inspector-link" href="${escapeHtml(data.source_url)}" rel="noreferrer" target="_blank">Open market</a>`
                    : ""
            }
        </div>
    `;

    const metadata = Array.isArray(data.metadata) ? data.metadata : [];
    const snippets = Array.isArray(data.evidence_snippets) ? data.evidence_snippets : [];
    const confidence = confidencePercent(data.confidence);
    const contextScore = Math.min(100, 18 + metadata.length * 18 + (data.summary ? 14 : 0));
    const supportScore = Math.min(100, snippets.length * 32);
    const clarityScore = data.summary ? Math.min(100, 24 + Math.min(data.summary.length, 140) / 2) : 20;

    return `
        <section class="inspector-panel">
            <p class="inspector-kicker">${escapeHtml(humanizeType(data.type))}</p>
            <h3 class="inspector-title">${escapeHtml(data.label)}</h3>
            ${summary}
            ${probabilityBlock}
            ${sourceBlock}
            ${actionBlock}

            <div class="bar-diagram">
                ${buildBar("Confidence", confidence, "How strong this signal looks in the current graph.", "accent")}
                ${buildBar("Context", Math.round(contextScore), "How much supporting metadata is attached.", "cool")}
                ${buildBar("Support", Math.round(supportScore), "How much evidence is attached to this node.", "warm")}
                ${buildBar("Clarity", Math.round(clarityScore), "How quickly this selection can be understood.", "violet")}
            </div>

            <section class="inspector-section">
                <p class="subheading">Quick facts</p>
                ${buildDetailItems([
                    { label: "Type", value: humanizeType(data.type) },
                    { label: "Confidence", value: `${confidence}%` },
                ])}
            </section>

            ${
                metadata.length
                    ? `
                        <section class="inspector-section">
                            <p class="subheading">Details</p>
                            ${buildDetailItems(metadata)}
                        </section>
                    `
                    : ""
            }

            ${buildEvidence(snippets)}
        </section>
    `;
}

function edgeDirectness(type) {
    const mapping = {
        affects_directly: 92,
        governed_by_rule: 86,
        supported_by: 78,
        involves: 70,
        related_to: 62,
        affects_indirectly: 58,
        mentions: 44,
    };
    return mapping[type] || 60;
}

function renderEdge(selection) {
    const data = selection.data;
    const confidence = confidencePercent(data.confidence);
    const directness = edgeDirectness(data.type);
    const explanationScore = data.explanation
        ? Math.min(100, 18 + Math.min(data.explanation.length, 180) / 2)
        : 24;
    const signal = Math.round((confidence + directness + explanationScore) / 3);

    return `
        <section class="inspector-panel">
            <p class="inspector-kicker">Relationship</p>
            <h3 class="inspector-title">${escapeHtml(data.source_label)} -> ${escapeHtml(data.target_label)}</h3>
            ${
                data.explanation
                    ? `<p class="inspector-copy">${escapeHtml(data.explanation)}</p>`
                    : ""
            }

            <div class="bar-diagram">
                ${buildBar("Confidence", confidence, "How strong this relationship is in the current graph.", "accent")}
                ${buildBar("Directness", directness, "Whether the link is direct, indirect, or rule-based.", "warm")}
                ${buildBar("Explanation", Math.round(explanationScore), "How much context is available for the connection.", "cool")}
                ${buildBar("Overall signal", signal, "A blended view for quick comparison.", "violet")}
            </div>

            <section class="inspector-section">
                <p class="subheading">Quick facts</p>
                ${buildDetailItems([
                    { label: "Relationship", value: humanizeRelationship(data.type) },
                    { label: "Confidence", value: `${confidence}%` },
                    { label: "Source", value: data.source_label || "" },
                    { label: "Target", value: data.target_label || "" },
                ])}
            </section>
        </section>
    `;
}

function renderSelectionHtml(selection) {
    if (!selection) {
        return renderEmpty();
    }

    if (selection.kind === "node") {
        return renderNode(selection);
    }

    return renderEdge(selection);
}

export function createInspectorController({ container }) {
    let lastKey = "";

    return {
        renderSelection(selection) {
            const nextKey = selection
                ? `${selection.kind}:${selection.data.id}:${selection.data.confidence ?? ""}`
                : "empty";

            if (nextKey === lastKey) {
                return;
            }

            lastKey = nextKey;
            container.innerHTML = renderSelectionHtml(selection);
            const next = container.firstElementChild || container;
            next.classList.add("is-entering");
            window.setTimeout(() => {
                next.classList.remove("is-entering");
            }, 220);
        },

        resetCache() {
            lastKey = "";
        },
    };
}

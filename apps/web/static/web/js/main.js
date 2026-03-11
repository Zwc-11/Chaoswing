import { requestGraphFromUrl } from "./api.js";
import { pulseStage } from "./animations.js";
import { bindControls } from "./controls.js";
import { createGraphController } from "./graph.js";
import { bindGraphToolbar } from "./graph-toolbar.js";
import { createInspectorController } from "./inspector.js";
import { createState } from "./state.js";
import {
    clearElement,
    createTag,
    formatCount,
    formatDate,
    humanizeRelationship,
    isEditableTarget,
    readInitialState,
} from "./utils.js";

function collectElements() {
    return {
        form: document.getElementById("graph-form"),
        urlInput: document.getElementById("event-url"),
        loadGraphButton: document.getElementById("load-graph-button"),
        edgeLabelToggle: document.getElementById("toggle-edge-labels"),
        nodeTypeInputs: [...document.querySelectorAll("[data-node-type]")],
        confidenceThreshold: document.getElementById("confidence-threshold"),
        confidenceThresholdValue: document.getElementById("confidence-threshold-value"),
        layoutSelector: document.getElementById("layout-selector"),
        refreshButtons: [document.getElementById("refresh-graph")],
        resetButtons: [document.getElementById("reset-view-top")],
        sampleButtons: [...document.querySelectorAll(".sample-link")],
        emptyLoadButton: document.getElementById("empty-load-button"),
        errorLoadSampleButton: document.getElementById("error-load-sample"),
        formFeedback: document.getElementById("form-feedback"),
        summaryEmpty: document.getElementById("summary-empty"),
        summaryContent: document.getElementById("summary-content"),
        summaryTitle: document.getElementById("summary-title"),
        summaryStatus: document.getElementById("summary-status"),
        summaryUpdated: document.getElementById("summary-updated"),
        summarySourceUrl: document.getElementById("summary-source-url"),
        summaryTags: document.getElementById("summary-tags"),
        summaryOutcomes: document.getElementById("summary-outcomes"),
        summaryRunId: document.getElementById("summary-run-id"),
        summaryRunMode: document.getElementById("summary-run-mode"),
        summaryRunReview: document.getElementById("summary-run-review"),
        stage: document.getElementById("graph-stage"),
        stageCopy: document.getElementById("stage-copy"),
        graphCanvas: document.getElementById("graph-canvas"),
        graphStats: document.getElementById("graph-stats"),
        emptyState: document.getElementById("graph-empty-state"),
        emptyStateTitle: document.getElementById("empty-state-title"),
        emptyStateCopy: document.getElementById("empty-state-copy"),
        loadingState: document.getElementById("graph-loading-state"),
        errorState: document.getElementById("graph-error-state"),
        errorCopy: document.getElementById("graph-error-copy"),
        inspectorContent: document.getElementById("inspector-content"),
        inspectorShellStatus: document.getElementById("inspector-shell-status"),
        selectionStateBadge: document.getElementById("selection-state-badge"),
        clearSelectionButton: document.getElementById("clear-selection"),
        fitViewButton: document.getElementById("fit-view"),
        relayoutButton: document.getElementById("relayout-graph"),
        toolbarEdgeLabelsButton: document.getElementById("toolbar-edge-labels"),
        focusStrongestPathButton: document.getElementById("focus-strongest-path"),
    };
}

function renderTokens(container, items, className = "tag") {
    clearElement(container);
    for (const item of items) {
        container.append(createTag(item, className));
    }
}

function renderSummary(elements, payload) {
    const event = payload?.event;
    const run = payload?.run;
    if (!event) {
        elements.summaryEmpty.hidden = false;
        elements.summaryContent.hidden = true;
        return;
    }

    elements.summaryEmpty.hidden = true;
    elements.summaryContent.hidden = false;
    elements.summaryTitle.textContent = event.title;
    elements.summaryStatus.textContent = event.status;
    elements.summaryUpdated.textContent = `Updated ${formatDate(event.updated_at)}`;
    elements.summarySourceUrl.href = event.source_url;
    elements.summarySourceUrl.textContent = event.source_url;
    renderTokens(elements.summaryTags, event.tags || []);
    renderTokens(elements.summaryOutcomes, event.outcomes || [], "tag tag--outcome");
    elements.summaryRunId.textContent = run?.id
        ? `Run ${run.id}`
        : "Run id appears here after Django persists the graph.";
    elements.summaryRunMode.textContent = run?.mode
        ? `Mode: ${run.mode}${run.model_name ? ` via ${run.model_name}` : ""}`
        : "";
    elements.summaryRunReview.textContent =
        run?.review?.quality_score !== undefined
            ? `Review quality score: ${Math.round(run.review.quality_score * 100)}%`
            : "";
}

function renderStageState(elements, state, graphStats) {
    elements.loadingState.hidden = !state.loading;
    elements.errorState.hidden = !state.error;

    if (state.error) {
        elements.errorCopy.textContent = state.error;
    }

    const showInitialEmpty = !state.hasLoaded && !state.loading && !state.error;
    const showFilteredEmpty = state.hasLoaded && !state.loading && !state.error && graphStats.nodes === 0;
    elements.emptyState.hidden = !(showInitialEmpty || showFilteredEmpty);

    if (showFilteredEmpty) {
        elements.emptyStateTitle.textContent = "Current filters suppress every visible node.";
        elements.emptyStateCopy.textContent =
            "Lower the confidence threshold or restore the default controls to bring the graph back.";
        elements.emptyLoadButton.dataset.mode = "restore";
        elements.emptyLoadButton.textContent = "Restore Default Filters";
        return;
    }

    elements.emptyStateTitle.textContent = "Load a Polymarket event to wake up the graph.";
    elements.emptyStateCopy.textContent =
        "Paste one market on the left. Hover any node to preview it, then click once to lock the graph while you read.";
    elements.emptyLoadButton.dataset.mode = "load";
    elements.emptyLoadButton.textContent = "Load Butterfly Graph";
}

function describeInteraction(interaction) {
    if (!interaction) {
        return "";
    }

    if (interaction.kind === "node") {
        return interaction.data.label || "node";
    }

    const relationship = humanizeRelationship(interaction.data.type || "relationship").toLowerCase();
    return `${interaction.data.source_label || "source"} ${relationship} ${interaction.data.target_label || "target"}`;
}

function renderInspectorChrome(elements, state) {
    if (state.selected) {
        elements.selectionStateBadge.className = "selection-state selection-state--locked";
        elements.selectionStateBadge.textContent = "Locked";
        elements.inspectorShellStatus.textContent = `${describeInteraction(state.selected)} is locked in focus mode.`;
        elements.clearSelectionButton.hidden = false;
        return;
    }

    if (state.hovered) {
        elements.selectionStateBadge.className = "selection-state selection-state--preview";
        elements.selectionStateBadge.textContent = state.hovered.previewActive ? "Preview" : "Recent";
        elements.inspectorShellStatus.textContent = state.hovered.previewActive
            ? `Previewing ${describeInteraction(state.hovered)}. Click to lock this view.`
            : `Last preview: ${describeInteraction(state.hovered)}. Hover another node or click to lock this one next time.`;
        elements.clearSelectionButton.hidden = false;
        return;
    }

    elements.selectionStateBadge.className = "selection-state selection-state--idle";
    elements.selectionStateBadge.textContent = state.hasLoaded ? "Ready" : "Idle";
    elements.inspectorShellStatus.textContent = state.hasLoaded
        ? "Hover a node for a quick preview, or click once to hold the graph steady while you read."
        : "Load one market first, then use hover to preview and click to lock.";
    elements.clearSelectionButton.hidden = true;
}

function renderFeedback(elements, state, graphStats) {
    delete elements.formFeedback.dataset.variant;

    if (state.error) {
        elements.formFeedback.dataset.variant = "error";
        elements.formFeedback.textContent = state.error;
        return;
    }

    if (state.loading) {
        elements.formFeedback.dataset.variant = "loading";
        elements.formFeedback.textContent = "Loading one market and preparing a fresh graph layout.";
        return;
    }

    if (state.hasLoaded) {
        if (state.selected) {
            elements.formFeedback.textContent = `Locked on ${describeInteraction(state.selected)}. Press Esc or use Clear focus to reset.`;
            return;
        }

        if (state.hovered) {
            elements.formFeedback.textContent = state.hovered.previewActive
                ? `Previewing ${describeInteraction(state.hovered)}. Click once to keep this view.`
                : `Last preview: ${describeInteraction(state.hovered)}. Hover another node or press Esc to clear.`;
            return;
        }

        elements.formFeedback.textContent = `Run ready with ${formatCount(
            graphStats.nodes,
            graphStats.edges
        )}.`;
        return;
    }

    elements.formFeedback.textContent = "Ready for a Polymarket event URL.";
}

function renderStageCopy(elements, state, graphStats) {
    if (state.loading) {
        elements.stageCopy.textContent = "Refreshing the graph with a new server response and layout pass.";
        return;
    }

    if (state.error) {
        elements.stageCopy.textContent =
            "The graph surface is still available. Fix the request or load one of the sample markets to continue.";
        return;
    }

    if (state.selected) {
        elements.stageCopy.textContent =
            state.selected.kind === "node"
                ? `Locked on ${state.selected.data.label}. Read the profile on the right, then clear focus when you want the full graph back.`
                : `Locked on ${describeInteraction(state.selected)}. The right panel now explains the relationship.`;
        return;
    }

    if (state.hovered) {
        elements.stageCopy.textContent =
            state.hovered.kind === "node"
                ? state.hovered.previewActive
                    ? `Previewing ${state.hovered.data.label}. Click once to lock the graph around this node.`
                    : `Last preview: ${state.hovered.data.label}. Hover again to re-highlight it, or click a node to lock the graph.`
                : state.hovered.previewActive
                    ? `Previewing ${describeInteraction(state.hovered)}. Click the edge to keep the relationship in focus.`
                    : `Last preview: ${describeInteraction(state.hovered)}. Hover again to re-highlight it, or click to lock a relationship.`;
        return;
    }

    if (state.payload?.event) {
        elements.stageCopy.textContent = `${state.payload.event.title} with ${formatCount(
            graphStats.nodes,
            graphStats.edges
        )} after live filtering.`;
        return;
    }

    elements.stageCopy.textContent =
        "A graph-first workspace for tracing direct drivers, evidence, related markets, and indirect impact paths.";
}

function flashAction(button) {
    button.classList.remove("is-flashed");
    requestAnimationFrame(() => {
        button.classList.add("is-flashed");
        window.setTimeout(() => {
            button.classList.remove("is-flashed");
        }, 400);
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const initialState = readInitialState("chaoswing-initial-state");
    const elements = collectElements();
    const store = createState(initialState.controls || {});
    const inspector = createInspectorController({
        container: elements.inspectorContent,
    });
    const graph = createGraphController({
        container: elements.graphCanvas,
        onSelect: (interaction) => {
            store.setInteraction(interaction);
            inspector.renderSelection(interaction.selected || interaction.hovered || null);
        },
    });

    elements.inspectorContent.addEventListener("click", (event) => {
        const button = event.target.closest("[data-action='center-node']");
        if (!button) {
            return;
        }

        const nodeId = button.dataset.nodeId || "";
        if (nodeId) {
            graph.centerOnNode(nodeId);
        }
    });

    elements.clearSelectionButton.addEventListener("click", () => {
        graph.clearSelection();
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || isEditableTarget(event.target)) {
            return;
        }

        if (store.getState().selected || store.getState().hovered) {
            event.preventDefault();
            graph.clearSelection();
        }
    });

    let graphStats = { nodes: 0, edges: 0 };

    async function loadGraph(url) {
        if (!url) {
            store.setError("Paste a full Polymarket event URL to load a ChaosWing run.");
            return;
        }

        store.setLoading(true);
        try {
            const payload = await requestGraphFromUrl(url, initialState.endpoints.graph);
            store.setPayload(payload, url);
            pulseStage(elements.stage);
        } catch (error) {
            store.setError(error.message);
        }
    }

    const controls = bindControls({
        store,
        graphController: graph,
        elements,
        loadGraph,
    });
    const toolbar = bindGraphToolbar({
        store,
        graphController: graph,
        elements,
        flashAction,
    });

    store.subscribe((state, previousState) => {
        controls.sync(state);
        toolbar.sync(state);

        if (state.payload !== previousState.payload || state.controls !== previousState.controls) {
            graphStats = graph.render(state.payload, state.controls);
        }

        if (state.payload !== previousState.payload) {
            inspector.resetCache();
            inspector.renderSelection(state.selected || state.hovered || null);
        }

        elements.graphStats.textContent = state.hasLoaded
            ? graphStats.nodes > 0
                ? formatCount(graphStats.nodes, graphStats.edges)
                : "0 visible nodes"
            : "No graph loaded yet";

        renderSummary(elements, state.payload);
        renderStageState(elements, state, graphStats);
        renderFeedback(elements, state, graphStats);
        renderStageCopy(elements, state, graphStats);
        renderInspectorChrome(elements, state);
    });

    controls.sync(store.getState());
    toolbar.sync(store.getState());
    renderSummary(elements, null);
    renderStageState(elements, store.getState(), graphStats);
    renderFeedback(elements, store.getState(), graphStats);
    renderStageCopy(elements, store.getState(), graphStats);
    renderInspectorChrome(elements, store.getState());
});

import { requestGraphFromUrl } from "./api.js";
import { exportGraphPayload, buildShareUrl, listGraphRuns } from "./api.js";
import { pulseStage } from "./animations.js";
import { bindControls } from "./controls.js";
import { createGraphController } from "./graph.js";
import { bindGraphToolbar } from "./graph-toolbar.js";
import { createInspectorController } from "./inspector.js";
import { createState } from "./state.js";
import { toast } from "./toast.js";
import {
    clearElement,
    createTag,
    formatCount,
    formatDate,
    humanizeRelationship,
    isEditableTarget,
    readInitialState,
} from "./utils.js";

// --- Element collection -------------------------------------------------------

function collectElements() {
    return {
        // Form / input
        form: document.getElementById("graph-form"),
        urlInput: document.getElementById("event-url"),
        loadGraphButton: document.getElementById("load-graph-button"),
        loadGraphButtonLabel: document.querySelector(
            "#load-graph-button .button__label",
        ),
        loadGraphButtonSpinner: document.querySelector(
            "#load-graph-button .button__spinner",
        ),

        // Controls
        edgeLabelToggle: document.getElementById("toggle-edge-labels"),
        nodeTypeInputs: [...document.querySelectorAll("[data-node-type]")],
        confidenceThreshold: document.getElementById("confidence-threshold"),
        confidenceThresholdValue: document.getElementById(
            "confidence-threshold-value",
        ),
        layoutSelector: document.getElementById("layout-selector"),

        // Command bar buttons
        refreshButtons: [document.getElementById("refresh-graph")],
        resetButtons: [document.getElementById("reset-view-top")],
        historyButton: document.getElementById("history-button"),
        exportButton: document.getElementById("export-button"),
        shareButton: document.getElementById("share-button"),
        briefButton: document.getElementById("brief-button"),
        shortcutsButton: document.getElementById("shortcuts-button"),

        // Sample / empty state
        sampleButtons: [...document.querySelectorAll(".sample-link")],
        emptyLoadButton: document.getElementById("empty-load-button"),
        errorLoadSampleButton: document.getElementById("error-load-sample"),
        errorRetryButton: document.getElementById("error-retry-button"),

        // Status / feedback
        formFeedback: document.getElementById("form-feedback"),
        liveIndicator: document.getElementById("live-indicator"),
        commandBar: document.getElementById("command-bar"),

        // Market summary
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

        // Graph stage
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

        // Loading steps
        loadStep1: document.getElementById("load-step-1"),
        loadStep2: document.getElementById("load-step-2"),
        loadStep3: document.getElementById("load-step-3"),
        loadStep4: document.getElementById("load-step-4"),

        // Inspector
        inspectorContent: document.getElementById("inspector-content"),
        inspectorShellStatus: document.getElementById("inspector-shell-status"),
        selectionStateBadge: document.getElementById("selection-state-badge"),
        clearSelectionButton: document.getElementById("clear-selection"),

        // Toolbar
        fitViewButton: document.getElementById("fit-view"),
        relayoutButton: document.getElementById("relayout-graph"),
        toolbarEdgeLabelsButton: document.getElementById("toolbar-edge-labels"),
        focusStrongestPathButton: document.getElementById(
            "focus-strongest-path",
        ),

        // Run history drawer
        historyBackdrop: document.getElementById("run-history-backdrop"),
        historyDrawer: document.getElementById("run-history-drawer"),
        closeHistoryButton: document.getElementById("close-history-button"),
        historyList: document.getElementById("run-history-list"),
        historyMeta: document.getElementById("run-history-meta"),
        historyCount: document.getElementById("history-count"),

        // Keyboard shortcut overlay
        shortcutOverlay: document.getElementById("shortcut-overlay"),
        closeShortcutsButton: document.getElementById("close-shortcuts-button"),

        // Toast container (already in DOM via template)
        toastContainer: document.getElementById("toast-container"),
    };
}

// --- Render helpers -----------------------------------------------------------

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
    renderTokens(
        elements.summaryOutcomes,
        event.outcomes || [],
        "tag tag--outcome",
    );
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
    const showFilteredEmpty =
        state.hasLoaded &&
        !state.loading &&
        !state.error &&
        graphStats.nodes === 0;
    elements.emptyState.hidden = !(showInitialEmpty || showFilteredEmpty);

    if (showFilteredEmpty) {
        elements.emptyStateTitle.textContent =
            "Current filters suppress every visible node.";
        elements.emptyStateCopy.textContent =
            "Lower the confidence threshold or restore the default controls to bring the graph back.";
        elements.emptyLoadButton.dataset.mode = "restore";
        elements.emptyLoadButton.textContent = "Restore Default Filters";
        return;
    }

        elements.emptyStateTitle.textContent =
            "Load a Polymarket event to wake up the graph.";
        elements.emptyStateCopy.textContent =
            "Paste one market on the left. ChaosWing will turn it into a live trader brief, then let you inspect the graph underneath it.";
        elements.emptyLoadButton.dataset.mode = "load";
        elements.emptyLoadButton.textContent = "Load Market Brief";
}

function describeInteraction(interaction) {
    if (!interaction) return "";
    if (interaction.kind === "node") return interaction.data.label || "node";
    const relationship = humanizeRelationship(
        interaction.data.type || "relationship",
    ).toLowerCase();
    return `${interaction.data.source_label || "source"} ${relationship} ${interaction.data.target_label || "target"}`;
}

function renderInspectorChrome(elements, state) {
    if (state.selected) {
        elements.selectionStateBadge.className =
            "selection-state selection-state--locked";
        elements.selectionStateBadge.textContent = "Locked";
        elements.inspectorShellStatus.textContent = `${describeInteraction(state.selected)} is locked in focus mode.`;
        elements.clearSelectionButton.hidden = false;
        return;
    }

    if (state.hovered) {
        elements.selectionStateBadge.className =
            "selection-state selection-state--preview";
        elements.selectionStateBadge.textContent = state.hovered.previewActive
            ? "Preview"
            : "Recent";
        elements.inspectorShellStatus.textContent = state.hovered.previewActive
            ? `Previewing ${describeInteraction(state.hovered)}. Click to lock this view.`
            : `Last preview: ${describeInteraction(state.hovered)}. Hover another node or click to lock.`;
        elements.clearSelectionButton.hidden = false;
        return;
    }

    elements.selectionStateBadge.className =
        "selection-state selection-state--idle";
    elements.selectionStateBadge.textContent = state.hasLoaded
        ? "Ready"
        : "Idle";
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
        elements.formFeedback.textContent =
            "Loading one market and preparing a fresh graph layout.";
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
                : `Last preview: ${describeInteraction(state.hovered)}.`;
            return;
        }
        elements.formFeedback.textContent = `Run ready with ${formatCount(graphStats.nodes, graphStats.edges)}.`;
        return;
    }

    elements.formFeedback.textContent = "Ready for a Polymarket event URL.";
}

function renderStageCopy(elements, state, graphStats) {
    if (state.loading) {
        elements.stageCopy.textContent =
            "Resolving the event, discovering related markets, and building your butterfly graph...";
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
                    : `Last preview: ${state.hovered.data.label}. Hover again or click to lock.`
                : state.hovered.previewActive
                  ? `Previewing ${describeInteraction(state.hovered)}. Click to keep it in focus.`
                  : `Last preview: ${describeInteraction(state.hovered)}.`;
        return;
    }
    if (state.payload?.event) {
        elements.stageCopy.textContent = `${state.payload.event.title} - ${formatCount(graphStats.nodes, graphStats.edges)} after live filtering.`;
        return;
    }
    elements.stageCopy.textContent =
        "A market-intelligence workspace for tracing direct drivers, evidence, related markets, and indirect spillover paths.";
}

function renderLoadingButton(elements, isLoading) {
    if (elements.loadGraphButton) {
        elements.loadGraphButton.disabled = isLoading;
    }
    if (elements.loadGraphButtonLabel) {
        elements.loadGraphButtonLabel.textContent = isLoading
            ? "Loading graph..."
            : "Load Market Brief";
    }
    if (elements.loadGraphButtonSpinner) {
        elements.loadGraphButtonSpinner.hidden = !isLoading;
    }
}

function renderLiveIndicator(elements, isLive) {
    if (elements.liveIndicator) {
        elements.liveIndicator.hidden = !isLive;
    }
}

function renderActionButtons(elements, payload) {
    const hasPayload = Boolean(payload);
    if (elements.exportButton) {
        elements.exportButton.disabled = !hasPayload;
    }
    if (elements.shareButton) {
        elements.shareButton.disabled = !hasPayload;
    }
    if (elements.briefButton) {
        elements.briefButton.disabled = !hasPayload;
    }
}

function flashAction(button) {
    if (!button) return;
    button.classList.remove("is-flashed");
    requestAnimationFrame(() => {
        button.classList.add("is-flashed");
        window.setTimeout(() => button.classList.remove("is-flashed"), 400);
    });
}

// --- Loading step animation ---------------------------------------------------

function createLoadingStepController(elements) {
    const steps = [
        elements.loadStep1,
        elements.loadStep2,
        elements.loadStep3,
        elements.loadStep4,
    ].filter(Boolean);

    let timers = [];
    let currentStep = -1;

    function reset() {
        timers.forEach((t) => clearTimeout(t));
        timers = [];
        currentStep = -1;
        steps.forEach((step) => {
            step.classList.remove("is-active", "is-done");
        });
    }

    function start() {
        reset();
        const delays = [0, 900, 2200, 3800];
        delays.forEach((delay, index) => {
            const t = setTimeout(() => {
                if (currentStep >= 0 && steps[currentStep]) {
                    steps[currentStep].classList.remove("is-active");
                    steps[currentStep].classList.add("is-done");
                }
                currentStep = index;
                if (steps[index]) {
                    steps[index].classList.add("is-active");
                }
            }, delay);
            timers.push(t);
        });
    }

    function complete() {
        timers.forEach((t) => clearTimeout(t));
        timers = [];
        steps.forEach((step) => {
            step.classList.remove("is-active");
            step.classList.add("is-done");
        });
    }

    return { start, complete, reset };
}

// --- Run history drawer -------------------------------------------------------

function createHistoryDrawer(elements, { onLoadUrl }) {
    let isOpen = false;
    let isAnimatingOut = false;

    function open() {
        if (isOpen || isAnimatingOut) return;
        isOpen = true;

        elements.historyDrawer.hidden = false;
        elements.historyBackdrop.hidden = false;

        // Reset animation classes
        elements.historyDrawer.classList.remove("is-closing");
        elements.historyBackdrop.classList.remove("is-closing");

        document.body.style.overflow = "hidden";

        // Focus trap: focus first interactive element
        const firstBtn =
            elements.historyDrawer.querySelector("button, [tabindex]");
        if (firstBtn) {
            requestAnimationFrame(() => firstBtn.focus());
        }
    }

    function close() {
        if (!isOpen || isAnimatingOut) return;
        isAnimatingOut = true;

        elements.historyDrawer.classList.add("is-closing");
        elements.historyBackdrop.classList.add("is-closing");

        const onEnd = () => {
            elements.historyDrawer.hidden = true;
            elements.historyBackdrop.hidden = true;
            elements.historyDrawer.classList.remove("is-closing");
            elements.historyBackdrop.classList.remove("is-closing");
            document.body.style.overflow = "";
            isOpen = false;
            isAnimatingOut = false;

            // Return focus to the history button
            if (elements.historyButton) {
                elements.historyButton.focus();
            }
        };

        elements.historyDrawer.addEventListener("animationend", onEnd, {
            once: true,
        });
        setTimeout(onEnd, 400); // Fallback
    }

    function toggle() {
        if (isOpen) {
            close();
        } else {
            open();
        }
    }

    // Bind internal close button
    if (elements.closeHistoryButton) {
        elements.closeHistoryButton.addEventListener("click", close);
    }

    // Backdrop click closes
    if (elements.historyBackdrop) {
        elements.historyBackdrop.addEventListener("click", close);
    }

    // Run item clicks in the history list
    if (elements.historyList) {
        elements.historyList.addEventListener("click", (event) => {
            const item = event.target.closest("[data-run-url]");
            if (!item) return;
            const url = item.dataset.runUrl;
            if (url) {
                close();
                onLoadUrl(url, { fromHistory: true });
            }
        });
    }

    return { open, close, toggle, isOpen: () => isOpen };
}

// --- Keyboard shortcut overlay -----------------------------------------------

function createShortcutOverlay(elements) {
    let isOpen = false;

    function open() {
        if (isOpen) return;
        isOpen = true;
        elements.shortcutOverlay.hidden = false;
        elements.shortcutOverlay.classList.remove("is-closing");
        const closeBtn = elements.closeShortcutsButton;
        if (closeBtn) requestAnimationFrame(() => closeBtn.focus());
    }

    function close() {
        if (!isOpen) return;
        elements.shortcutOverlay.classList.add("is-closing");
        const onEnd = () => {
            elements.shortcutOverlay.hidden = true;
            elements.shortcutOverlay.classList.remove("is-closing");
            isOpen = false;
            if (elements.shortcutsButton) elements.shortcutsButton.focus();
        };
        elements.shortcutOverlay.addEventListener("animationend", onEnd, {
            once: true,
        });
        setTimeout(onEnd, 300);
    }

    function toggle() {
        if (isOpen) close();
        else open();
    }

    if (elements.closeShortcutsButton) {
        elements.closeShortcutsButton.addEventListener("click", close);
    }

    // Click outside panel closes
    if (elements.shortcutOverlay) {
        elements.shortcutOverlay.addEventListener("click", (event) => {
            if (event.target === elements.shortcutOverlay) close();
        });
    }

    return { open, close, toggle, isOpen: () => isOpen };
}

// --- URL query-param auto-load ------------------------------------------------

function getAutoLoadUrl() {
    const params = new URLSearchParams(window.location.search);
    return params.get("url") || "";
}

// --- Main entry point ---------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
    const initialState = readInitialState("chaoswing-initial-state");
    const elements = collectElements();
    const store = createState(initialState.controls || {});
    const loadingSteps = createLoadingStepController(elements);

    const inspector = createInspectorController({
        container: elements.inspectorContent,
    });

    const graph = createGraphController({
        container: elements.graphCanvas,
        onSelect: (interaction) => {
            store.setInteraction(interaction);
            inspector.renderSelection(
                interaction.selected || interaction.hovered || null,
            );
        },
    });

    // -- History drawer ------------------------------------------------------
    const historyDrawer = createHistoryDrawer(elements, {
        onLoadUrl: (url, options = {}) => {
            if (elements.urlInput) elements.urlInput.value = url;
            if (options.fromHistory) {
                toast.info(`Loading saved run...`);
            }
            loadGraph(url);
        },
    });

    // -- Keyboard shortcut overlay -------------------------------------------
    const shortcutOverlay = createShortcutOverlay(elements);

    // -- Inspector node center button ----------------------------------------
    elements.inspectorContent.addEventListener("click", (event) => {
        const button = event.target.closest("[data-action='center-node']");
        if (!button) return;
        const nodeId = button.dataset.nodeId || "";
        if (nodeId) graph.centerOnNode(nodeId);
    });

    // -- Clear selection -----------------------------------------------------
    elements.clearSelectionButton.addEventListener("click", () => {
        graph.clearSelection();
    });

    // -- History button ------------------------------------------------------
    if (elements.historyButton) {
        elements.historyButton.addEventListener("click", () => {
            historyDrawer.toggle();
        });
    }

    // -- Export button -------------------------------------------------------
    if (elements.exportButton) {
        elements.exportButton.addEventListener("click", () => {
            const payload = store.getState().payload;
            if (!payload) {
                toast.warning("Load a graph first before exporting.");
                return;
            }
            try {
                exportGraphPayload(payload);
                toast.success("Graph exported as JSON.");
                flashAction(elements.exportButton);
            } catch (err) {
                toast.error("Export failed. Check the browser console.");
                console.error("Export error:", err);
            }
        });
    }

    // -- Share button --------------------------------------------------------
    if (elements.shareButton) {
        elements.shareButton.addEventListener("click", async () => {
            const payload = store.getState().payload;
            if (!payload) {
                toast.warning("Load a graph first before sharing.");
                return;
            }
            const dashboardUrl =
                initialState?.endpoints?.graph?.replace(
                    "/api/v1/graph/from-url/",
                    "/app/",
                ) || "/app/";
            const url = buildShareUrl(payload, dashboardUrl);
            try {
                await navigator.clipboard.writeText(url);
                toast.success("Link copied to clipboard.");
                flashAction(elements.shareButton);
            } catch {
                // Fallback: show URL in toast
                toast.info(`Share URL: ${url}`);
            }
        });
    }

    // -- Brief button -------------------------------------------------------
    if (elements.briefButton) {
        elements.briefButton.addEventListener("click", () => {
            const payload = store.getState().payload;
            const briefUrl = payload?.run?.brief_url;
            if (!briefUrl) {
                toast.warning("Load a graph first before opening the brief.");
                return;
            }
            window.location.href = briefUrl;
        });
    }

    // -- Shortcuts button ----------------------------------------------------
    if (elements.shortcutsButton) {
        elements.shortcutsButton.addEventListener("click", () => {
            shortcutOverlay.toggle();
        });
    }

    // -- Error retry button --------------------------------------------------
    if (elements.errorRetryButton) {
        elements.errorRetryButton.addEventListener("click", () => {
            const url = store.getState().currentUrl;
            if (url) loadGraph(url);
        });
    }

    // -- Keyboard shortcuts --------------------------------------------------
    document.addEventListener("keydown", (event) => {
        const state = store.getState();

        // Esc: clear selection or close overlays
        if (event.key === "Escape") {
            if (shortcutOverlay.isOpen()) {
                event.preventDefault();
                shortcutOverlay.close();
                return;
            }
            if (historyDrawer.isOpen()) {
                event.preventDefault();
                historyDrawer.close();
                return;
            }
            if (!isEditableTarget(event.target)) {
                if (state.selected || state.hovered) {
                    event.preventDefault();
                    graph.clearSelection();
                }
            }
            return;
        }

        // Ignore shortcuts when typing in an input
        if (isEditableTarget(event.target)) return;

        switch (event.key) {
            case "?":
                event.preventDefault();
                shortcutOverlay.toggle();
                break;
            case "h":
            case "H":
                event.preventDefault();
                historyDrawer.toggle();
                break;
            default:
                break;
        }
    });

    // -- Graph stats tracking ------------------------------------------------
    let graphStats = { nodes: 0, edges: 0 };

    // -- Core graph load function --------------------------------------------
    async function loadGraph(url) {
        if (!url) {
            toast.warning("Paste a full Polymarket event URL to load a run.");
            store.setError(
                "Paste a full Polymarket event URL to load a ChaosWing run.",
            );
            return;
        }

        store.setLoading(true);
        loadingSteps.start();

        if (elements.commandBar) {
            elements.commandBar.classList.add("is-loading");
        }

        const loadingToast = toast.loading("Resolving Polymarket event...");

        try {
            const payload = await requestGraphFromUrl(
                url,
                initialState.endpoints.graph,
            );
            loadingSteps.complete();
            store.setPayload(payload, url);
            pulseStage(elements.stage);

            const nodeCount = payload?.graph?.nodes?.length ?? 0;
            const edgeCount = payload?.graph?.edges?.length ?? 0;
            const title = payload?.event?.title || "Graph";
            loadingToast.resolve(
                `${title} - ${nodeCount} nodes  |  ${edgeCount} edges`,
            );

            // Refresh history count badge
            refreshHistoryBadge();
        } catch (error) {
            loadingSteps.reset();
            store.setError(error.message);
            loadingToast.reject(error.message);
        } finally {
            if (elements.commandBar) {
                elements.commandBar.classList.remove("is-loading");
            }
        }
    }

    // -- Refresh history count badge -----------------------------------------
    async function refreshHistoryBadge() {
        try {
            const data = await listGraphRuns(initialState.endpoints.runs, {
                limit: 1,
            });
            const total = data?.total ?? 0;
            if (elements.historyCount) {
                elements.historyCount.textContent = String(total);
                elements.historyCount.hidden = total === 0;
            }
            if (elements.historyMeta) {
                elements.historyMeta.textContent =
                    total > 0
                        ? `${total} saved graph${total === 1 ? "" : "s"}`
                        : "No runs yet";
            }
        } catch {
            // Non-critical; silently ignore
        }
    }

    // -- Bind controls -------------------------------------------------------
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

    // -- State subscription --------------------------------------------------
    store.subscribe((state, previousState) => {
        controls.sync(state);
        toolbar.sync(state);

        if (
            state.payload !== previousState.payload ||
            state.controls !== previousState.controls
        ) {
            graphStats = graph.render(state.payload, state.controls);
        }

        if (state.payload !== previousState.payload) {
            inspector.resetCache();
            inspector.renderSelection(state.selected || state.hovered || null);
        }

        // Loading button state
        if (state.loading !== previousState.loading) {
            renderLoadingButton(elements, state.loading);
        }

        // Live indicator
        renderLiveIndicator(elements, state.hasLoaded && !state.loading);

        // Action buttons (export / share)
        renderActionButtons(elements, state.payload);

        // Graph stats display
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

    // -- Initial render ------------------------------------------------------
    controls.sync(store.getState());
    toolbar.sync(store.getState());
    renderLoadingButton(elements, false);
    renderLiveIndicator(elements, false);
    renderActionButtons(elements, null);
    renderSummary(elements, null);
    renderStageState(elements, store.getState(), graphStats);
    renderFeedback(elements, store.getState(), graphStats);
    renderStageCopy(elements, store.getState(), graphStats);
    renderInspectorChrome(elements, store.getState());

    // -- Auto-load from URL query param --------------------------------------
    const autoUrl = getAutoLoadUrl();
    if (autoUrl) {
        if (elements.urlInput) elements.urlInput.value = autoUrl;
        // Small delay to let the page finish rendering first
        setTimeout(() => loadGraph(autoUrl), 200);
    }
});


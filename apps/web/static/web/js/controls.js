export function bindControls({ store, graphController, elements, loadGraph }) {
    function currentUrlOrFallback() {
        return elements.urlInput.value.trim() || store.getState().currentUrl;
    }

    elements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        loadGraph(elements.urlInput.value.trim());
    });

    elements.sampleButtons.forEach((button) => {
        button.addEventListener("click", () => {
            const url = button.dataset.url || "";
            elements.urlInput.value = url;
            loadGraph(url);
        });
    });

    elements.emptyLoadButton.addEventListener("click", () => {
        if (elements.emptyLoadButton.dataset.mode === "restore") {
            store.resetControls();
            graphController.resetView();
            return;
        }

        const nextUrl = currentUrlOrFallback() || elements.sampleButtons[0]?.dataset.url || "";
        if (nextUrl) {
            elements.urlInput.value = nextUrl;
            loadGraph(nextUrl);
        }
    });

    elements.errorLoadSampleButton.addEventListener("click", () => {
        const nextUrl = elements.sampleButtons[0]?.dataset.url || "";
        if (nextUrl) {
            elements.urlInput.value = nextUrl;
            loadGraph(nextUrl);
        }
    });

    elements.refreshButtons.forEach((button) => {
        button.addEventListener("click", () => {
            const url = currentUrlOrFallback();
            if (url) {
                loadGraph(url);
            }
        });
    });

    elements.resetButtons.forEach((button) => {
        button.addEventListener("click", () => {
            graphController.resetView();
        });
    });

    elements.edgeLabelToggle.addEventListener("change", () => {
        store.updateControls({ showEdgeLabels: elements.edgeLabelToggle.checked });
    });

    elements.nodeTypeInputs.forEach((input) => {
        input.addEventListener("change", () => {
            store.updateControls({
                nodeFilters: {
                    ...store.getState().controls.nodeFilters,
                    [input.dataset.nodeType]: input.checked,
                },
            });
        });
    });

    elements.confidenceThreshold.addEventListener("input", () => {
        store.updateControls({
            confidenceThreshold: Number(elements.confidenceThreshold.value) / 100,
        });
    });

    elements.layoutSelector.addEventListener("change", () => {
        store.updateControls({ layout: elements.layoutSelector.value });
    });

    return {
        sync(state) {
            const isBusy = state.loading;
            const hasGraph = Boolean(state.payload);
            const primaryLabel = isBusy ? "Resolving market..." : "Load Butterfly Graph";
            elements.loadGraphButton.disabled = isBusy;
            elements.loadGraphButton.textContent = primaryLabel;
            elements.edgeLabelToggle.checked = state.controls.showEdgeLabels;
            elements.confidenceThreshold.value = String(
                Math.round(state.controls.confidenceThreshold * 100)
            );
            elements.confidenceThresholdValue.textContent = `${Math.round(
                state.controls.confidenceThreshold * 100
            )}%`;
            elements.layoutSelector.value = state.controls.layout;
            elements.nodeTypeInputs.forEach((input) => {
                input.checked = state.controls.nodeFilters[input.dataset.nodeType] !== false;
            });
            elements.refreshButtons.forEach((button) => {
                button.disabled = isBusy || !currentUrlOrFallback();
            });
            elements.resetButtons.forEach((button) => {
                button.disabled = isBusy || !hasGraph;
            });
            elements.sampleButtons.forEach((button) => {
                button.disabled = isBusy;
            });
            elements.emptyLoadButton.disabled = isBusy;
            elements.errorLoadSampleButton.disabled = isBusy;
        },
    };
}

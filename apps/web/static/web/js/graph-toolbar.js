import { isEditableTarget } from "./utils.js";

export function bindGraphToolbar({ store, graphController, elements, flashAction }) {
    function fitView() {
        if (elements.fitViewButton.disabled) {
            return;
        }
        graphController.fit();
        flashAction(elements.fitViewButton);
    }

    function relayout() {
        if (elements.relayoutButton.disabled) {
            return;
        }
        graphController.relayout(store.getState().controls.layout);
        flashAction(elements.relayoutButton);
    }

    function toggleLabels() {
        if (elements.toolbarEdgeLabelsButton.disabled) {
            return;
        }
        store.updateControls({
            showEdgeLabels: !store.getState().controls.showEdgeLabels,
        });
        flashAction(elements.toolbarEdgeLabelsButton);
    }

    function focusStrongestPath() {
        if (elements.focusStrongestPathButton.disabled) {
            return;
        }
        const didFocus = graphController.focusStrongestPath();
        if (didFocus) {
            flashAction(elements.focusStrongestPathButton);
        }
    }

    elements.fitViewButton.addEventListener("click", fitView);
    elements.relayoutButton.addEventListener("click", relayout);
    elements.toolbarEdgeLabelsButton.addEventListener("click", toggleLabels);
    elements.focusStrongestPathButton.addEventListener("click", focusStrongestPath);

    document.addEventListener("keydown", (event) => {
        if (isEditableTarget(event.target)) {
            return;
        }

        const key = event.key.toLowerCase();
        if (key === "f") {
            event.preventDefault();
            fitView();
        }
        if (key === "r") {
            event.preventDefault();
            relayout();
        }
        if (key === "l") {
            event.preventDefault();
            toggleLabels();
        }
    });

    return {
        sync(state) {
            const hasGraph = Boolean(state.payload);
            const isBusy = state.loading;
            elements.fitViewButton.disabled = isBusy || !hasGraph;
            elements.relayoutButton.disabled = isBusy || !hasGraph;
            elements.focusStrongestPathButton.disabled = isBusy || !hasGraph;
            elements.toolbarEdgeLabelsButton.disabled = isBusy || !hasGraph;
            elements.toolbarEdgeLabelsButton.dataset.active = String(state.controls.showEdgeLabels);
        },
    };
}

function buildCollection(cy, nodeIds, edgeIds) {
    const collection = cy.collection();
    for (const nodeId of nodeIds) {
        collection.merge(cy.getElementById(nodeId));
    }
    for (const edgeId of edgeIds) {
        collection.merge(cy.getElementById(edgeId));
    }
    return collection;
}

export function createGraphEffects(cy) {
    let pulseTimer = null;
    let selectedId = "";
    let focusedPath = { nodeIds: [], edgeIds: [] };
    let pathLocked = false;

    function stopPulse() {
        if (pulseTimer) {
            window.clearInterval(pulseTimer);
            pulseTimer = null;
        }
        cy.elements().removeClass("path-pulse");
    }

    function resetClasses() {
        cy.elements().removeClass(
            "dimmed highlighted-node highlighted-edge selected-node selected-edge selected-neighbor path"
        );
        stopPulse();
    }

    function applyNodeHover(node) {
        cy.elements().addClass("dimmed");
        node.removeClass("dimmed").addClass("highlighted-node");
        node.connectedEdges().removeClass("dimmed").addClass("highlighted-edge");
        node.connectedEdges().connectedNodes().removeClass("dimmed");
    }

    function applyNodeSelection(node) {
        cy.elements().addClass("dimmed");
        node.removeClass("dimmed").addClass("selected-node");
        node.connectedEdges().removeClass("dimmed").addClass("highlighted-edge");
        node.connectedEdges().connectedNodes().removeClass("dimmed").addClass("selected-neighbor");
    }

    function applyEdgeSelection(edge) {
        cy.elements().addClass("dimmed");
        edge.removeClass("dimmed").addClass("selected-edge");
        edge.connectedNodes().removeClass("dimmed").addClass("selected-neighbor");
    }

    function startPathPulse(collection) {
        let enabled = false;
        pulseTimer = window.setInterval(() => {
            enabled = !enabled;
            collection.toggleClass("path-pulse", enabled);
        }, 620);
    }

    return {
        hoverNode(node) {
            if (selectedId || pathLocked) {
                return;
            }
            resetClasses();
            applyNodeHover(node);
        },

        clearHover() {
            if (selectedId || pathLocked) {
                return;
            }
            resetClasses();
        },

        select(target) {
            selectedId = target.id();
            pathLocked = false;
            focusedPath = { nodeIds: [], edgeIds: [] };
            resetClasses();

            if (target.isNode()) {
                applyNodeSelection(target);
                return;
            }

            applyEdgeSelection(target);
        },

        clear() {
            selectedId = "";
            pathLocked = false;
            focusedPath = { nodeIds: [], edgeIds: [] };
            resetClasses();
        },

        focusStrongestPath(nodeIds, edgeIds) {
            selectedId = "";
            pathLocked = true;
            focusedPath = { nodeIds, edgeIds };
            resetClasses();
            cy.elements().addClass("dimmed");
            const collection = buildCollection(cy, nodeIds, edgeIds);
            collection.removeClass("dimmed").addClass("path");
            startPathPulse(collection);
            return collection;
        },

        restore() {
            if (pathLocked && focusedPath.edgeIds.length) {
                return this.focusStrongestPath(focusedPath.nodeIds, focusedPath.edgeIds);
            }

            if (!selectedId) {
                resetClasses();
                return null;
            }

            const selected = cy.getElementById(selectedId);
            if (!selected || selected.length === 0) {
                selectedId = "";
                resetClasses();
                return null;
            }

            resetClasses();
            if (selected.isNode()) {
                applyNodeSelection(selected);
            } else {
                applyEdgeSelection(selected);
            }
            return selected;
        },
    };
}

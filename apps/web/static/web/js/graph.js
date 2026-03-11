import { createGraphEffects } from "./graph-effects.js";
import { humanizeRelationship, readCssVariable } from "./utils.js";

const NODE_STYLE_MAP = {
    Event: { color: "--node-event", size: 72, glow: 28, borderWidth: 4 },
    Entity: { color: "--node-entity", size: 46, glow: 14, borderWidth: 3 },
    RelatedMarket: { color: "--node-related-market", size: 48, glow: 14, borderWidth: 3 },
    Evidence: { color: "--node-evidence", size: 42, glow: 11, borderWidth: 3 },
    Rule: { color: "--node-rule", size: 40, glow: 12, borderWidth: 3 },
    Hypothesis: { color: "--node-hypothesis", size: 44, glow: 12, borderWidth: 3 },
};

const EDGE_STYLE_MAP = {
    mentions: { lineStyle: "dashed", scoreBonus: 0.02 },
    involves: { lineStyle: "solid", scoreBonus: 0.03 },
    supported_by: { lineStyle: "solid", scoreBonus: 0.02 },
    related_to: { lineStyle: "solid", scoreBonus: 0.04 },
    affects_directly: { lineStyle: "solid", scoreBonus: 0.08 },
    affects_indirectly: { lineStyle: "dashed", scoreBonus: 0.05 },
    governed_by_rule: { lineStyle: "dotted", scoreBonus: -0.02 },
};

const TYPE_LAYOUT_BIAS = {
    Event: 12,
    Entity: 4,
    RelatedMarket: 3,
    Evidence: 2,
    Rule: 1,
    Hypothesis: 0,
};

function getNodeVisual(type) {
    const visual = NODE_STYLE_MAP[type] || NODE_STYLE_MAP.Entity;
    return {
        color: readCssVariable(visual.color),
        glow: visual.glow,
        borderWidth: visual.borderWidth,
        size: visual.size,
    };
}

function getEdgeVisual(type) {
    return EDGE_STYLE_MAP[type] || EDGE_STYLE_MAP.related_to;
}

function escapeSvg(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function svgDataUri(markup) {
    return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(markup)}`;
}

function nodeInitials(label) {
    const parts = String(label || "")
        .replace(/[^A-Za-z0-9+]+/g, " ")
        .trim()
        .split(/\s+/)
        .filter(Boolean);

    if (!parts.length) {
        return "CW";
    }

    if (parts.length === 1) {
        return parts[0].slice(0, 2).toUpperCase();
    }

    return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
}

function buildDisplayIcon(iconUrl, label, accentColor) {
    const initials = escapeSvg(nodeInitials(label));
    const safeAccent = escapeSvg(accentColor || "#58d7ff");
    const safeIconUrl = escapeSvg(iconUrl || "");
    const imageLayer = safeIconUrl
        ? `
            <defs>
                <clipPath id="clip">
                    <circle cx="64" cy="64" r="58" />
                </clipPath>
            </defs>
            <image href="${safeIconUrl}" x="6" y="6" width="116" height="116" preserveAspectRatio="xMidYMid slice" clip-path="url(#clip)" />
            <circle cx="64" cy="64" r="58" fill="rgba(5, 5, 8, 0.16)" />
        `
        : `<circle cx="64" cy="64" r="58" fill="${safeAccent}" fill-opacity="0.14" />`;

    return svgDataUri(`
        <svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
            <rect width="128" height="128" rx="64" fill="#050505" />
            ${imageLayer}
            <circle cx="64" cy="64" r="58" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="2" />
            <rect x="40" y="42" width="48" height="42" rx="14" fill="rgba(7, 12, 18, 0.84)" stroke="${safeAccent}" stroke-width="2.5" />
            <text x="64" y="69" text-anchor="middle" fill="#ffffff" font-size="25" font-family="Inter, Arial, sans-serif" font-weight="700">${initials}</text>
        </svg>
    `);
}

function getNodeScale(nodeCount) {
    if (nodeCount <= 12) {
        return 1.14;
    }
    if (nodeCount <= 18) {
        return 1.08;
    }
    if (nodeCount <= 26) {
        return 1;
    }
    return 0.92;
}

function getFitPadding(nodeCount) {
    if (nodeCount <= 12) {
        return 44;
    }
    if (nodeCount <= 18) {
        return 56;
    }
    if (nodeCount <= 28) {
        return 68;
    }
    return 84;
}

function getTheme() {
    return {
        canvas: "#050505",
        text: readCssVariable("--color-text"),
        labelBg: "rgba(10, 10, 12, 0.8)",
        labelBorder: "rgba(255, 255, 255, 0.1)",
        edgeRest: "rgba(255, 255, 255, 0.1)",
        edgeHighlight: "rgba(255, 255, 255, 0.8)",
    };
}

function createStyles(showEdgeLabels) {
    const theme = getTheme();

    return [
        {
            selector: "node",
            style: {
                "background-color": theme.canvas,
                "background-image": "data(displayIconUrl)",
                "background-fit": "cover",
                "background-repeat": "no-repeat",
                "background-width": "100%",
                "background-height": "100%",
                "background-position-x": "50%",
                "background-position-y": "50%",
                "background-image-opacity": 1,
                "background-image-containment": "inside",
                "background-clip": "node",
                shape: "ellipse",
                width: "data(size)",
                height: "data(size)",
                label: "data(label)",
                color: theme.text,
                "font-family": "Inter, sans-serif",
                "font-size": 12,
                "font-weight": 500,
                "text-wrap": "wrap",
                "text-max-width": 188,
                "text-valign": "bottom",
                "text-halign": "center",
                "text-margin-y": 10,
                "text-background-opacity": 1,
                "text-background-color": theme.labelBg,
                "text-background-padding": "7px",
                "text-background-shape": "roundrectangle",
                "text-border-opacity": 1,
                "text-border-width": 1,
                "text-border-color": theme.labelBorder,
                "text-events": "yes",
                "border-width": "data(borderWidth)",
                "border-color": "data(color)",
                "shadow-color": "data(color)",
                "shadow-opacity": 0.5,
                "shadow-blur": "data(glow)",
                "overlay-opacity": 0,
                "transition-property": "width, height, shadow-opacity, shadow-blur, border-width",
                "transition-duration": "300ms",
            },
        },
        {
            selector: "edge",
            style: {
                width: 1.5,
                "curve-style": "bezier",
                "line-style": "data(lineStyle)",
                "line-color": theme.edgeRest,
                "target-arrow-color": theme.edgeRest,
                "target-arrow-shape": "triangle",
                "arrow-scale": 1.1,
                label: "data(label)",
                "font-family": "Inter, sans-serif",
                "font-size": 10,
                color: theme.text,
                "text-background-color": theme.labelBg,
                "text-background-opacity": 0.96,
                "text-background-padding": 4,
                "text-border-width": 1,
                "text-border-color": theme.labelBorder,
                "text-events": "yes",
                "text-rotation": "autorotate",
                "text-margin-y": -12,
                "text-opacity": showEdgeLabels ? 0.95 : 0,
                opacity: 0.78,
                "overlay-opacity": 0,
                "transition-property": "opacity, line-color, target-arrow-color, text-opacity, width",
                "transition-duration": "300ms",
            },
        },
        {
            selector: ".dimmed",
            style: {
                opacity: 0.1,
            },
        },
        {
            selector: ".highlighted-node, .selected-node",
            style: {
                "shadow-opacity": 1,
                "shadow-blur": 30,
                "border-width": 4,
            },
        },
        {
            selector: ".selected-neighbor, .path",
            style: {
                opacity: 1,
                "shadow-opacity": 0.7,
                "shadow-blur": 28,
            },
        },
        {
            selector: ".highlighted-edge, .selected-edge, .path",
            style: {
                opacity: 1,
                width: 2.5,
                "line-color": theme.edgeHighlight,
                "target-arrow-color": theme.edgeHighlight,
                "text-opacity": 1,
                "z-index": 999,
            },
        },
        {
            selector: "node.path-pulse",
            style: {
                "shadow-opacity": 1,
                "shadow-blur": 40,
            },
        },
        {
            selector: "edge.path-pulse",
            style: {
                width: 3,
                opacity: 1,
                "line-color": theme.edgeHighlight,
                "target-arrow-color": theme.edgeHighlight,
            },
        },
    ];
}

function buildLayoutMap(nodes, edges) {
    const eventNode = nodes.find((node) => node.type === "Event");
    const depths = new Map(nodes.map((node) => [node.id, 4]));

    if (!eventNode) {
        return depths;
    }

    depths.set(eventNode.id, 0);
    const queue = [eventNode.id];
    const adjacency = new Map();

    for (const edge of edges) {
        if (!adjacency.has(edge.source)) {
            adjacency.set(edge.source, []);
        }
        if (!adjacency.has(edge.target)) {
            adjacency.set(edge.target, []);
        }
        adjacency.get(edge.source).push(edge.target);
        adjacency.get(edge.target).push(edge.source);
    }

    while (queue.length) {
        const currentId = queue.shift();
        const currentDepth = depths.get(currentId) || 0;

        for (const nextId of adjacency.get(currentId) || []) {
            if ((depths.get(nextId) || Infinity) <= currentDepth + 1) {
                continue;
            }
            depths.set(nextId, currentDepth + 1);
            queue.push(nextId);
        }
    }

    return depths;
}

function applyLayoutMetadata(nodes, edges) {
    const depths = buildLayoutMap(nodes, edges);

    return nodes.map((node) => {
        const depth = depths.get(node.id) ?? 4;
        return {
            ...node,
            layoutWeight: 120 - depth * 24 + (TYPE_LAYOUT_BIAS[node.type] || 0),
        };
    });
}

function buildLayoutOptions(layoutName, rootId) {
    if (layoutName === "concentric") {
        return {
            name: "concentric",
            animate: true,
            animationDuration: 520,
            fit: true,
            padding: 68,
            avoidOverlap: true,
            minNodeSpacing: 92,
            spacingFactor: 1.24,
            concentric: (node) => Number(node.data("layoutWeight") || 1),
            levelWidth: () => 20,
        };
    }

    if (layoutName === "breadthfirst") {
        return {
            name: "breadthfirst",
            animate: true,
            animationDuration: 520,
            fit: true,
            directed: true,
            roots: rootId ? `#${rootId}` : undefined,
            spacingFactor: 1.12,
            padding: 72,
        };
    }

    return {
        name: "cose",
        animate: true,
        animationDuration: 640,
        fit: true,
        padding: 72,
        nodeRepulsion: 18000,
        nodeOverlap: 32,
        idealEdgeLength: 205,
        edgeElasticity: 120,
        gravity: 0.18,
        numIter: 1600,
    };
}

function visibleGraphFromPayload(payload, controls) {
    if (!payload) {
        return { nodes: [], edges: [] };
    }

    const assets = payload.assets || {};

    const scale = getNodeScale(payload.graph.nodes.length);
    const typedNodes = payload.graph.nodes
        .filter((node) => controls.nodeFilters[node.type] !== false)
        .map((node) => {
            const visual = getNodeVisual(node.type);
            const iconUrl = assets[node.icon_key] || "";
            return {
                ...node,
                ...visual,
                size: Math.round(visual.size * scale),
                iconUrl,
                displayIconUrl: buildDisplayIcon(iconUrl, node.label, visual.color),
            };
        });

    const typedNodeIds = new Set(typedNodes.map((node) => node.id));
    const typedNodeLookup = Object.fromEntries(typedNodes.map((node) => [node.id, node]));
    const thresholdEdges = payload.graph.edges
        .filter(
            (edge) =>
                edge.confidence >= controls.confidenceThreshold &&
                typedNodeIds.has(edge.source) &&
                typedNodeIds.has(edge.target)
        )
        .map((edge) => {
            const visual = getEdgeVisual(edge.type);
            return {
                ...edge,
                color: "",
                lineStyle: visual.lineStyle,
                label: humanizeRelationship(edge.type),
                source_label: typedNodeLookup[edge.source]?.label || edge.source,
                target_label: typedNodeLookup[edge.target]?.label || edge.target,
            };
        });

    const connectedNodeIds = new Set();
    for (const edge of thresholdEdges) {
        connectedNodeIds.add(edge.source);
        connectedNodeIds.add(edge.target);
    }

    const filteredNodes = typedNodes.filter(
        (node) => node.type === "Event" || connectedNodeIds.has(node.id)
    );
    const nodeIds = new Set(filteredNodes.map((node) => node.id));
    const edges = thresholdEdges.filter(
        (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)
    );
    const nodes = applyLayoutMetadata(filteredNodes, edges);
    return { nodes, edges };
}

function toCyElements(graph) {
    return [
        ...graph.nodes.map((node) => ({
            data: {
                id: node.id,
                label: node.label,
                type: node.type,
                confidence: node.confidence,
                summary: node.summary,
                metadata: node.metadata || [],
                evidence_snippets: node.evidence_snippets || [],
                source_url: node.source_url || "",
                source_title: node.source_title || "",
                source_description: node.source_description || "",
                probability: node.probability ?? null,
                probability_label: node.probability_label || "",
                iconUrl: node.iconUrl || "",
                displayIconUrl: node.displayIconUrl || node.iconUrl || "",
                color: node.color,
                size: node.size,
                glow: node.glow,
                borderWidth: node.borderWidth,
                layoutWeight: node.layoutWeight,
            },
        })),
        ...graph.edges.map((edge) => ({
            data: {
                id: edge.id,
                source: edge.source,
                target: edge.target,
                type: edge.type,
                confidence: edge.confidence,
                explanation: edge.explanation,
                label: edge.label,
                color: edge.color,
                lineStyle: edge.lineStyle,
                source_label: edge.source_label,
                target_label: edge.target_label,
            },
        })),
    ];
}

function toSelection(target) {
    const data = target.data();
    if (target.isNode()) {
        return {
            kind: "node",
            data: {
                id: data.id,
                label: data.label,
                type: data.type,
                confidence: data.confidence,
                summary: data.summary,
                metadata: data.metadata,
                evidence_snippets: data.evidence_snippets,
                source_url: data.source_url,
                source_title: data.source_title,
                source_description: data.source_description,
                probability: data.probability,
                probability_label: data.probability_label,
                icon_url: data.iconUrl,
            },
        };
    }

    return {
        kind: "edge",
        data: {
            id: data.id,
            type: data.type,
            confidence: data.confidence,
            explanation: data.explanation,
            source_label: data.source_label,
            target_label: data.target_label,
        },
    };
}

function strongestPathFromGraph(graph) {
    const eventNode = graph.nodes.find((node) => node.type === "Event");
    if (!eventNode) {
        return { nodeIds: [], edgeIds: [] };
    }

    const adjacency = new Map();
    for (const edge of graph.edges) {
        if (!adjacency.has(edge.source)) {
            adjacency.set(edge.source, []);
        }
        if (!adjacency.has(edge.target)) {
            adjacency.set(edge.target, []);
        }
        adjacency.get(edge.source).push({ edge, nextId: edge.target });
        adjacency.get(edge.target).push({ edge, nextId: edge.source });
    }

    let best = { score: -Infinity, nodeIds: [eventNode.id], edgeIds: [] };

    function walk(currentId, visited, pathNodes, pathEdges) {
        if (pathEdges.length >= 2) {
            const score = pathEdges.reduce(
                (total, edge) => total + edge.confidence + getEdgeVisual(edge.type).scoreBonus,
                0
            );
            if (score > best.score) {
                best = {
                    score,
                    nodeIds: [...pathNodes],
                    edgeIds: pathEdges.map((edge) => edge.id),
                };
            }
        }

        if (pathEdges.length === 4) {
            return;
        }

        for (const neighbor of adjacency.get(currentId) || []) {
            if (visited.has(neighbor.nextId)) {
                continue;
            }
            visited.add(neighbor.nextId);
            pathNodes.push(neighbor.nextId);
            pathEdges.push(neighbor.edge);
            walk(neighbor.nextId, visited, pathNodes, pathEdges);
            pathEdges.pop();
            pathNodes.pop();
            visited.delete(neighbor.nextId);
        }
    }

    walk(eventNode.id, new Set([eventNode.id]), [eventNode.id], []);
    return best;
}

export function createGraphController({ container, onSelect }) {
    if (!window.cytoscape) {
        throw new Error("Cytoscape.js is unavailable.");
    }

    const cy = window.cytoscape({
        container,
        elements: [],
        style: createStyles(false),
        layout: { name: "preset" },
        wheelSensitivity: 0.18,
        minZoom: 0.38,
        maxZoom: 2.4,
    });

    const effects = createGraphEffects(cy);
    let visibleGraph = { nodes: [], edges: [] };
    let selectedElementId = "";
    let activeLayout = "concentric";
    let lastHoveredSelection = null;

    function applyReadableViewport(nodeCount = visibleGraph.nodes.length) {
        if (!cy.elements().length) {
            return;
        }

        cy.fit(cy.elements(), getFitPadding(nodeCount));

        const zoomBoost =
            nodeCount <= 12 ? 1.14 : nodeCount <= 18 ? 1.1 : nodeCount <= 28 ? 1.05 : 1;
        if (zoomBoost > 1) {
            cy.zoom(Math.min(cy.maxZoom(), cy.zoom() * zoomBoost));
            cy.center();
        }
    }

    function runLayout(layoutName = activeLayout) {
        if (!cy.elements().length) {
            return;
        }

        const rootNode = visibleGraph.nodes.find((node) => node.type === "Event");
        const layout = cy.layout(buildLayoutOptions(layoutName, rootNode?.id));
        layout.on("layoutstop", () => {
            applyReadableViewport();
        });
        layout.run();
    }

    function emitHover(target, previewActive = true) {
        if (selectedElementId) {
            return;
        }
        if (target) {
            lastHoveredSelection = {
                ...toSelection(target),
                previewActive,
            };
        } else if (lastHoveredSelection) {
            lastHoveredSelection = {
                ...lastHoveredSelection,
                previewActive,
            };
        }
        onSelect({ selected: null, hovered: lastHoveredSelection });
    }

    function emitSelection(target) {
        lastHoveredSelection = null;
        onSelect({
            selected: target ? toSelection(target) : null,
            hovered: null,
        });
    }

    cy.on("tap", "node, edge", (event) => {
        selectedElementId = event.target.id();
        effects.select(event.target);
        emitSelection(event.target);
    });

    cy.on("tap", (event) => {
        if (event.target !== cy) {
            return;
        }
        selectedElementId = "";
        effects.clear();
        cy.elements().unselect();
        emitSelection(null);
    });

    cy.on("mouseover", "node", (event) => {
        effects.hoverNode(event.target);
        emitHover(event.target, true);
    });

    cy.on("mouseout", "node", () => {
        effects.clearHover();
        emitHover(null, false);
    });

    return {
        render(payload, controls) {
            activeLayout = controls.layout;
            lastHoveredSelection = null;
            visibleGraph = visibleGraphFromPayload(payload, controls);
            cy.elements().remove();
            cy.add(toCyElements(visibleGraph));
            cy.style(createStyles(controls.showEdgeLabels)).update();

            if (visibleGraph.nodes.length) {
                runLayout(controls.layout);
            }

            if (selectedElementId) {
                const selected = cy.getElementById(selectedElementId);
                if (selected.length > 0) {
                    effects.select(selected);
                } else {
                    selectedElementId = "";
                    effects.restore();
                    emitSelection(null);
                }
            } else {
                effects.restore();
            }

            return {
                nodes: visibleGraph.nodes.length,
                edges: visibleGraph.edges.length,
            };
        },

        fit() {
            applyReadableViewport();
        },

        relayout(layoutName = activeLayout) {
            activeLayout = layoutName;
            runLayout(layoutName);
        },

        resetView() {
            this.clearSelection();
            applyReadableViewport();
        },

        clearSelection() {
            selectedElementId = "";
            lastHoveredSelection = null;
            effects.clear();
            cy.elements().unselect();
            emitSelection(null);
        },

        focusStrongestPath() {
            if (!visibleGraph.nodes.length || !visibleGraph.edges.length) {
                return false;
            }
            const strongestPath = strongestPathFromGraph(visibleGraph);
            if (!strongestPath.edgeIds.length) {
                return false;
            }
            selectedElementId = "";
            emitSelection(null);
            const collection = effects.focusStrongestPath(
                strongestPath.nodeIds,
                strongestPath.edgeIds
            );
            cy.fit(collection, 116);
            return true;
        },

        centerOnNode(nodeId) {
            const node = cy.getElementById(nodeId);
            if (!node || node.length === 0 || !node.isNode()) {
                return false;
            }

            selectedElementId = node.id();
            effects.select(node);
            emitSelection(node);

            cy.animate({
                fit: {
                    eles: node.closedNeighborhood(),
                    padding: 92,
                },
                duration: 380,
            });
            return true;
        },
    };
}

import { deepClone } from "./utils.js";

export function createState(defaultControls) {
    let state = {
        loading: false,
        error: "",
        hasLoaded: false,
        currentUrl: "",
        payload: null,
        selected: null,
        hovered: null,
        controls: deepClone(defaultControls),
    };

    const listeners = new Set();

    function patch(nextState) {
        const previousState = state;
        state = nextState;
        for (const listener of listeners) {
            listener(state, previousState);
        }
    }

    return {
        getState() {
            return state;
        },

        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },

        setLoading(isLoading) {
            patch({
                ...state,
                loading: isLoading,
                error: isLoading ? "" : state.error,
                selected: isLoading ? null : state.selected,
                hovered: isLoading ? null : state.hovered,
            });
        },

        setError(message) {
            patch({
                ...state,
                loading: false,
                error: message,
            });
        },

        setPayload(payload, currentUrl) {
            patch({
                ...state,
                loading: false,
                error: "",
                hasLoaded: true,
                currentUrl,
                payload,
                selected: null,
                hovered: null,
            });
        },

        setSelection(selection) {
            patch({
                ...state,
                selected: selection,
                hovered: selection ? null : state.hovered,
            });
        },

        setHovered(selection) {
            patch({
                ...state,
                hovered: state.selected ? null : selection,
            });
        },

        setInteraction({ selected = null, hovered = null }) {
            patch({
                ...state,
                selected,
                hovered: selected ? null : hovered,
            });
        },

        updateControls(controlPatch) {
            patch({
                ...state,
                controls: {
                    ...state.controls,
                    ...controlPatch,
                },
            });
        },

        resetControls() {
            patch({
                ...state,
                controls: deepClone(defaultControls),
            });
        },
    };
}

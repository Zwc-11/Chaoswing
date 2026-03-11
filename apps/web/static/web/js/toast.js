/* =======================================================================
   ChaosWing - Toast Notification System
   Self-contained toast module. Import and call show() anywhere.
   ======================================================================= */

const ICONS = {
    success: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="20 6 9 17 4 12"/>
    </svg>`,
    error: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>`,
    info: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="16" x2="12" y2="12"/>
        <line x1="12" y1="8" x2="12.01" y2="8"/>
    </svg>`,
    loading: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true" class="toast__spin-icon">
        <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>`,
    warning: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
        <line x1="12" y1="9" x2="12" y2="13"/>
        <line x1="12" y1="17" x2="12.01" y2="17"/>
    </svg>`,
};

const DEFAULT_DURATIONS = {
    success: 4000,
    error: 6000,
    info: 4000,
    warning: 5000,
    loading: 0, // Loading toasts are manually dismissed
};

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function createToastElement({ message, type, duration, dismissible }) {
    const toast = document.createElement("div");
    toast.className = `toast toast--${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    toast.setAttribute("aria-live", type === "error" ? "assertive" : "polite");
    toast.setAttribute("aria-atomic", "true");

    const icon = ICONS[type] || ICONS.info;

    const progressBar =
        duration > 0
            ? `<div class="toast__progress" aria-hidden="true">
                   <div class="toast__progress-fill" style="--toast-duration: ${duration}ms;"></div>
               </div>`
            : "";

    const closeButton = dismissible
        ? `<button class="toast__close" type="button" aria-label="Dismiss notification">
               <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">
                   <line x1="18" y1="6" x2="6" y2="18"/>
                   <line x1="6" y1="6" x2="18" y2="18"/>
               </svg>
           </button>`
        : "";

    toast.innerHTML = `
        <div class="toast__inner">
            <span class="toast__icon toast__icon--${type}">${icon}</span>
            <span class="toast__message">${escapeHtml(message)}</span>
            ${closeButton}
        </div>
        ${progressBar}
    `;

    return toast;
}

export function createToastSystem() {
    let container = document.getElementById("toast-container");
    const activeToasts = new Map();
    let toastCounter = 0;

    function ensureContainer() {
        if (!container) {
            container = document.createElement("div");
            container.id = "toast-container";
            container.className = "toast-container";
            container.setAttribute("role", "region");
            container.setAttribute("aria-label", "Notifications");
            container.setAttribute("aria-live", "assertive");
            container.setAttribute("aria-atomic", "false");
            document.body.appendChild(container);
        }
        return container;
    }

    function dismiss(toast, id) {
        if (!toast || !toast.isConnected) {
            activeToasts.delete(id);
            return;
        }

        toast.classList.add("is-leaving");

        const onEnd = () => {
            if (toast.isConnected) {
                toast.remove();
            }
            activeToasts.delete(id);
        };

        toast.addEventListener("animationend", onEnd, { once: true });
        // Fallback in case animationend doesn't fire
        setTimeout(onEnd, 400);
    }

    /**
     * Show a toast notification.
     *
     * @param {object} options
     * @param {string} options.message       The notification text.
     * @param {'success'|'error'|'info'|'warning'|'loading'} [options.type='info']
     * @param {number}  [options.duration]   Override auto-dismiss duration in ms. 0 = no auto-dismiss.
     * @param {boolean} [options.dismissible=true]  Show the close button.
     *
     * @returns {{ id: string, dismiss: () => void, update: (opts: object) => void }}
     */
    function show({
        message,
        type = "info",
        duration,
        dismissible = true,
    }) {
        const c = ensureContainer();
        const id = `toast-${++toastCounter}`;
        const autoDuration =
            duration !== undefined ? duration : DEFAULT_DURATIONS[type] ?? 4000;

        const toast = createToastElement({
            message,
            type,
            duration: autoDuration,
            dismissible,
        });

        toast.id = id;
        c.appendChild(toast);

        // Wire up close button
        const closeBtn = toast.querySelector(".toast__close");
        if (closeBtn) {
            closeBtn.addEventListener("click", () => dismiss(toast, id));
        }

        // Pause progress on hover
        toast.addEventListener("mouseenter", () => {
            toast.classList.add("is-paused");
        });
        toast.addEventListener("mouseleave", () => {
            toast.classList.remove("is-paused");
        });

        // Auto dismiss
        let timer = null;
        if (autoDuration > 0) {
            timer = setTimeout(() => dismiss(toast, id), autoDuration);
        }

        activeToasts.set(id, { toast, timer });

        return {
            id,
            dismiss() {
                if (timer) clearTimeout(timer);
                dismiss(toast, id);
            },
            update({ message: nextMessage, type: nextType } = {}) {
                if (nextMessage !== undefined) {
                    const msgEl = toast.querySelector(".toast__message");
                    if (msgEl) msgEl.textContent = nextMessage;
                }
                if (nextType !== undefined && nextType !== type) {
                    toast.className = `toast toast--${nextType}`;
                    const iconEl = toast.querySelector(".toast__icon");
                    if (iconEl) {
                        iconEl.className = `toast__icon toast__icon--${nextType}`;
                        iconEl.innerHTML = ICONS[nextType] || "";
                    }
                }
            },
        };
    }

    /** Convenience wrappers */
    const success = (message, options = {}) =>
        show({ ...options, message, type: "success" });

    const error = (message, options = {}) =>
        show({ ...options, message, type: "error" });

    const info = (message, options = {}) =>
        show({ ...options, message, type: "info" });

    const warning = (message, options = {}) =>
        show({ ...options, message, type: "warning" });

    /**
     * Show a loading toast that can be resolved to success or error later.
     *
     * @example
     * const t = toast.loading("Generating graph...");
     * try {
     *   await doWork();
     *   t.resolve("Graph ready.");
     * } catch {
     *   t.reject("Something went wrong.");
     * }
     */
    function loading(message, options = {}) {
        const handle = show({
            ...options,
            message,
            type: "loading",
            duration: 0,
            dismissible: false,
        });

        return {
            ...handle,
            resolve(successMessage) {
                handle.dismiss();
                success(successMessage || "Done.");
            },
            reject(errorMessage) {
                handle.dismiss();
                error(errorMessage || "Something went wrong.");
            },
        };
    }

    /** Dismiss all active toasts. */
    function dismissAll() {
        for (const [id, { toast, timer }] of activeToasts) {
            if (timer) clearTimeout(timer);
            dismiss(toast, id);
        }
    }

    return {
        show,
        success,
        error,
        info,
        warning,
        loading,
        dismissAll,
    };
}

/** Module-level singleton - import this throughout the app. */
export const toast = createToastSystem();


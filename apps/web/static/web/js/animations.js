const EASING = "cubic-bezier(0.2, 0.8, 0.2, 1)";

export async function transitionPanelContent(container, html) {
    const current = container.firstElementChild || container;
    if (current?.animate) {
        await current
            .animate(
                [
                    { opacity: 1, transform: "translateY(0)" },
                    { opacity: 0, transform: "translateY(10px)" },
                ],
                { duration: 120, easing: EASING, fill: "forwards" }
            )
            .finished.catch(() => undefined);
    }

    container.innerHTML = html;
    const next = container.firstElementChild || container;
    next.classList.add("is-entering");
    next
        ?.animate?.(
            [
                { opacity: 0, transform: "translateY(14px)" },
                { opacity: 1, transform: "translateY(0)" },
            ],
            { duration: 220, easing: EASING, fill: "both" }
        )
        .finished.catch(() => undefined);
}

export function pulseStage(stage) {
    stage.classList.remove("is-energized");
    requestAnimationFrame(() => {
        stage.classList.add("is-energized");
    });
}

export function setBusyClass(element, isBusy) {
    element.classList.toggle("is-busy", isBusy);
}

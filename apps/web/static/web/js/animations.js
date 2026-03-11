const EASING = "cubic-bezier(0.2, 0.8, 0.2, 1)";

export function pulseStage(stage) {
    stage.classList.remove("is-energized");
    requestAnimationFrame(() => {
        stage.classList.add("is-energized");
    });
}

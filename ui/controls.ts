/** Native Jellyfin action chrome shared by configuration sections and feedback. */
export function adminButton(
    label: string,
    action: ((event: MouseEvent) => unknown) | null,
    emphasis: "raised" | "quiet" = "raised",
): HTMLButtonElement {
    const control = document.createElement("button");
    control.type = "button";
    control.setAttribute("is", "emby-button");
    control.className = emphasis === "quiet" ? "emby-button button-flat" : "raised emby-button";
    const text = document.createElement("span");
    text.textContent = label;
    control.append(text);
    control.disabled = action === null;
    control.onclick = (event) => {
        void action?.(event);
    };
    return control;
}

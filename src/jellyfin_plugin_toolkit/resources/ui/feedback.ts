/** Shared feedback ownership and semantics; presentation belongs to each host. */
export type FeedbackSeverity = "info" | "success" | "progress" | "warning" | "error";
export interface FeedbackAction {
    label: string;
    run: () => unknown;
    disabled?: boolean;
    destructive?: boolean;
    busy?: boolean;
}
export interface FeedbackPresentation {
    message: HTMLElement;
    secondary: HTMLElement;
    actions: HTMLElement;
    diagnostics: HTMLElement;
    severity: (severity: FeedbackSeverity) => void;
}
export interface FeedbackAdapter {
    presentation: (root: HTMLElement) => FeedbackPresentation;
    button: (action: FeedbackAction) => HTMLButtonElement;
}
export interface FeedbackOptions {
    message: string;
    secondary?: string;
    severity?: FeedbackSeverity;
    actions?: FeedbackAction[];
    details?: string[];
    ttl?: number;
    expire?: () => void;
}
export class FeedbackScope {
    private timers = new Map<HTMLElement, ReturnType<typeof setTimeout>>();
    private views = new Map<HTMLElement, FeedbackPresentation>();
    private adapter: FeedbackAdapter;
    constructor(adapter: FeedbackAdapter) {
        this.adapter = adapter;
    }
    update(root: HTMLElement, options: FeedbackOptions): FeedbackPresentation {
        for (const existing of this.views.keys()) if (existing !== root && !existing.isConnected) this.forget(existing);
        this.clearTimer(root);
        const previous = this.views.get(root);
        const view = previous && root.contains(previous.message) ? previous : this.adapter.presentation(root);
        this.views.set(root, view);
        root.hidden = !options.message;
        const severity = options.severity || "info";
        root.dataset.severity = severity;
        root.setAttribute("role", severity === "error" || severity === "warning" ? "alert" : "status");
        root.setAttribute("aria-live", severity === "error" ? "assertive" : "polite");
        view.severity(severity);
        if (view.message.textContent !== options.message) view.message.textContent = options.message;
        if (view.secondary.textContent !== (options.secondary || "")) view.secondary.textContent = options.secondary || "";
        view.secondary.hidden = !options.secondary;
        const controls = (options.actions || []).map((action, index) => {
            const fresh = this.adapter.button(action);
            const existing = view.actions.children[index];
            if (existing instanceof HTMLButtonElement && existing.textContent === fresh.textContent) {
                existing.onclick = fresh.onclick;
                existing.disabled = fresh.disabled;
                existing.className = fresh.className;
                existing.setAttribute("aria-busy", fresh.getAttribute("aria-busy") || "false");
                return existing;
            }
            return fresh;
        });
        for (let index = 0; index < controls.length; index++) {
            const control = controls[index];
            if (control && view.actions.children[index] !== control) view.actions.insertBefore(control, view.actions.children[index] || null);
        }
        while (view.actions.children.length > controls.length) view.actions.lastElementChild?.remove();
        view.actions.hidden = !view.actions.childElementCount;
        const details = options.details || [];
        if (view.diagnostics.dataset.signature !== JSON.stringify(details)) {
            view.diagnostics.dataset.signature = JSON.stringify(details);
            const expanded = view.diagnostics.querySelector("details")?.open || false;
            const focused = view.diagnostics.contains(document.activeElement);
            view.diagnostics.replaceChildren();
            if (details.length) {
                const disclosure = document.createElement("details");
                disclosure.open = expanded;
                const heading = document.createElement("summary");
                heading.textContent = "Details";
                disclosure.append(heading);
                for (const text of details) {
                    const line = document.createElement("p");
                    line.textContent = text;
                    disclosure.append(line);
                }
                view.diagnostics.append(disclosure);
                if (focused) heading.focus({ preventScroll: true });
            }
        }
        view.diagnostics.hidden = !details.length;
        const ttl = options.ttl ?? (severity === "success" ? 4000 : 0);
        if (ttl && options.message)
            this.timers.set(
                root,
                setTimeout(() => {
                    this.clear(root);
                    options.expire?.();
                }, ttl),
            );
        return view;
    }
    clear(root: HTMLElement): void {
        this.clearTimer(root);
        root.hidden = true;
    }
    forget(root: HTMLElement): void {
        this.clearTimer(root);
        this.views
            .get(root)
            ?.actions.querySelectorAll("button")
            .forEach((button) => {
                button.onclick = null;
            });
        this.views.delete(root);
    }
    releaseWithin(parent: HTMLElement): void {
        for (const root of this.views.keys()) if (parent === root || parent.contains(root)) this.forget(root);
    }
    dispose(): void {
        for (const root of this.views.keys()) {
            this.forget(root);
            root.hidden = true;
        }
    }
    private clearTimer(root: HTMLElement): void {
        clearTimeout(this.timers.get(root));
        this.timers.delete(root);
    }
}
/** Confirmation outcomes are explicit: false keeps the confirmation open. */
export async function confirmFeedback(action: () => unknown, complete: () => void, fail: (cause: unknown) => void): Promise<void> {
    try {
        if ((await action()) !== false) complete();
    } catch (cause) {
        fail(cause);
    }
}

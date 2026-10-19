/* This Source Code Form is subject to the Mozilla Public License, v. 2.0.
 * See https://mozilla.org/MPL/2.0/. */
/** Resources owned by one bridge or renderer installation. Disposal is idempotent. */
export class Lifecycle {
    private readonly cleanups = new Set<() => void>();
    private readonly frames = new Set<number>();
    private readonly timers = new Set<number>();
    private readonly intervals = new Set<number>();
    private readonly listeners = new WeakMap<
        EventTarget,
        Set<{ type: string; listener: EventListenerOrEventListenerObject; capture: boolean; release: () => void }>
    >();
    private disposed = false;
    get isDisposed(): boolean {
        return this.disposed;
    }
    get resourceCount(): number {
        return this.cleanups.size + this.frames.size + this.timers.size + this.intervals.size;
    }
    snapshot(): Readonly<{ disposed: boolean; cleanups: number; frames: number; timers: number; intervals: number }> {
        return {
            disposed: this.disposed,
            cleanups: this.cleanups.size,
            frames: this.frames.size,
            timers: this.timers.size,
            intervals: this.intervals.size,
        };
    }
    own(cleanup: () => void): () => void {
        let active = true;
        const release = () => {
            if (!active) return;
            active = false;
            this.cleanups.delete(release);
            cleanup();
        };
        if (this.disposed) release();
        else this.cleanups.add(release);
        return release;
    }
    on<K extends keyof (GlobalEventHandlersEventMap & DocumentEventMap & WindowEventMap)>(
        target: EventTarget,
        type: K,
        listener: (event: (GlobalEventHandlersEventMap & DocumentEventMap & WindowEventMap)[K]) => void,
        options?: boolean | AddEventListenerOptions,
    ): () => void {
        // The event name supplies the DOM event type; listen owns its registration.
        return this.listen(target, type, listener as EventListener, options);
    }
    off<K extends keyof (GlobalEventHandlersEventMap & DocumentEventMap & WindowEventMap)>(
        target: EventTarget,
        type: K,
        listener: (event: (GlobalEventHandlersEventMap & DocumentEventMap & WindowEventMap)[K]) => void,
        options?: boolean | EventListenerOptions,
    ): void {
        this.unlisten(target, type, listener as EventListener, options);
    }
    listen(target: EventTarget, type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions): () => void {
        if (this.disposed) return () => {};
        const signal = typeof options === "object" ? options.signal : undefined;
        if (signal?.aborted) return () => {};
        const capture = typeof options === "boolean" ? options : !!options?.capture;
        let registrations = this.listeners.get(target);
        const existing =
            registrations && [...registrations].find((entry) => entry.type === type && entry.listener === listener && entry.capture === capture);
        if (existing) return existing.release;
        if (!registrations) {
            registrations = new Set();
            this.listeners.set(target, registrations);
        }
        const entries = registrations;
        const once = typeof options === "object" && options.once;
        const installed: EventListener = (event) => {
            if (once) entry.release();
            if (typeof listener === "function") listener.call(target, event);
            else listener.handleEvent(event);
        };
        const entry = { type, listener, capture, release: () => {} };
        entry.release = this.own(() => {
            signal?.removeEventListener("abort", entry.release);
            target.removeEventListener(type, installed, capture);
            entries.delete(entry);
            if (entries.size === 0) this.listeners.delete(target);
        });
        entries.add(entry);
        target.addEventListener(type, installed, options);
        signal?.addEventListener("abort", entry.release, { once: true });
        return entry.release;
    }
    unlisten(target: EventTarget, type: string, listener: EventListenerOrEventListenerObject, options?: boolean | EventListenerOptions): void {
        const capture = typeof options === "boolean" ? options : !!options?.capture;
        const entry = [...(this.listeners.get(target) ?? [])].find(
            (value) => value.type === type && value.listener === listener && value.capture === capture,
        );
        if (entry) entry.release();
        else target.removeEventListener(type, listener, options);
    }
    observer(callback: MutationCallback): MutationObserver {
        const observer = new MutationObserver(callback);
        this.own(() => observer.disconnect());
        return observer;
    }
    frame(callback: FrameRequestCallback): number {
        if (this.disposed) return 0;
        const id = requestAnimationFrame((time) => {
            this.frames.delete(id);
            if (!this.disposed) callback(time);
        });
        this.frames.add(id);
        return id;
    }
    cancelFrame(id: number): void {
        cancelAnimationFrame(id);
        this.frames.delete(id);
    }
    timeout(callback: () => void, delay = 0): number {
        if (this.disposed) return 0;
        const id = window.setTimeout(() => {
            this.timers.delete(id);
            if (!this.disposed) callback();
        }, delay);
        this.timers.add(id);
        return id;
    }
    clearTimeout(id: number): void {
        window.clearTimeout(id);
        this.timers.delete(id);
    }
    /** Yielding work must settle when its owning installation ends. */
    sleep(delay = 0): Promise<boolean> {
        if (this.disposed) return Promise.resolve(false);
        return new Promise((resolve) => {
            let timer = 0,
                completed = false;
            const release = this.own(() => {
                this.clearTimeout(timer);
                resolve(completed);
            });
            timer = this.timeout(() => {
                completed = true;
                release();
            }, delay);
        });
    }
    interval(callback: () => void, delay: number): number {
        if (this.disposed) return 0;
        const id = window.setInterval(() => {
            if (!this.disposed) callback();
        }, delay);
        this.intervals.add(id);
        return id;
    }
    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        const errors: unknown[] = [];
        const attempt = (cleanup: () => void) => {
            try {
                cleanup();
            } catch (error) {
                errors.push(error);
            }
        };
        for (const cleanup of [...this.cleanups]) attempt(cleanup);
        for (const id of this.frames) attempt(() => cancelAnimationFrame(id));
        for (const id of this.timers) attempt(() => window.clearTimeout(id));
        for (const id of this.intervals) attempt(() => window.clearInterval(id));
        this.frames.clear();
        this.timers.clear();
        this.intervals.clear();
        if (errors.length) throw new AggregateError(errors, "Lifecycle cleanup failed");
    }
}

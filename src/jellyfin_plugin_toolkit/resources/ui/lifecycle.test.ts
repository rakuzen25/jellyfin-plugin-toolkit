import assert from "node:assert/strict";
import { test } from "node:test";
import { Lifecycle } from "./lifecycle.ts";

test("listener ownership deduplicates registration and releases repeated temporary hooks", () => {
    const scope = new Lifecycle(),
        target = new EventTarget();
    let calls = 0;
    const listener = () => calls++;
    for (let index = 0; index < 100; index++) {
        scope.listen(target, "fixture", listener);
        scope.listen(target, "fixture", listener);
        assert.equal(scope.resourceCount, 1);
        target.dispatchEvent(new Event("fixture"));
        scope.unlisten(target, "fixture", listener);
        assert.equal(scope.resourceCount, 0);
    }
    assert.equal(calls, 100);
    scope.listen(target, "fixture", listener, { once: true });
    target.dispatchEvent(new Event("fixture"));
    assert.equal(scope.resourceCount, 0);
    assert.equal(calls, 101);
    scope.listen(target, "fixture", listener);
    scope.dispose();
    scope.dispose();
    target.dispatchEvent(new Event("fixture"));
    assert.equal(calls, 101);
    assert.equal(scope.resourceCount, 0);
});

test("disposal settles a queued cooperative yield and normal completion releases its owner", async (t) => {
    const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
    t.after(() => {
        if (previous) Object.defineProperty(globalThis, "window", previous);
        else Reflect.deleteProperty(globalThis, "window");
    });
    Object.defineProperty(globalThis, "window", { configurable: true, value: { setTimeout, clearTimeout, clearInterval } });
    const cancelled = new Lifecycle();
    const pending = cancelled.sleep(1000);
    cancelled.dispose();
    assert.equal(await pending, false);
    assert.equal(cancelled.resourceCount, 0);
    const completed = new Lifecycle();
    assert.equal(await completed.sleep(0), true);
    assert.equal(completed.resourceCount, 0);
    completed.dispose();
});

test("disposal releases later cleanups, listeners and timers before aggregating failures", (t) => {
    const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
    Object.defineProperty(globalThis, "window", { configurable: true, value: { setTimeout, clearTimeout, setInterval, clearInterval } });
    t.after(() => {
        if (previous) Object.defineProperty(globalThis, "window", previous);
        else Reflect.deleteProperty(globalThis, "window");
    });
    const scope = new Lifecycle(),
        target = new EventTarget();
    let cleaned = false,
        calls = 0;
    scope.own(() => {
        throw new Error("first failure");
    });
    scope.listen(target, "fixture", () => calls++);
    scope.own(() => {
        cleaned = true;
    });
    scope.own(() => {
        throw new Error("second failure");
    });
    scope.timeout(() => calls++, 1000);
    scope.interval(() => calls++, 1000);
    assert.throws(
        () => scope.dispose(),
        (error: unknown) => error instanceof AggregateError && error.errors.length === 2,
    );
    target.dispatchEvent(new Event("fixture"));
    assert.equal(calls, 0);
    assert.equal(cleaned, true);
    assert.equal(scope.resourceCount, 0);
    scope.dispose();
});

test("abort releases listener ownership and permits a fresh registration", () => {
    const scope = new Lifecycle(),
        target = new EventTarget(),
        controller = new AbortController();
    let calls = 0;
    const listener = () => calls++;
    scope.listen(target, "fixture", listener, { signal: controller.signal });
    controller.abort();
    assert.equal(scope.resourceCount, 0);
    scope.listen(target, "fixture", listener, { signal: controller.signal });
    assert.equal(scope.resourceCount, 0);
    scope.listen(target, "fixture", listener);
    target.dispatchEvent(new Event("fixture"));
    assert.equal(calls, 1);
    scope.dispose();
});

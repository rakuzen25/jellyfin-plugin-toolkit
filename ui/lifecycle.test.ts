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

test("disposal settles a queued cooperative yield and normal completion releases its owner", async () => {
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

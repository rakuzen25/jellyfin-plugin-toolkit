import assert from "node:assert/strict";
import { test } from "node:test";
import { confirmFeedback } from "../ui/feedback.ts";

test("confirmations close only on success and report failures without closing", async () => {
    let closed = 0;
    const failures = [];
    const complete = () => closed++;
    const fail = (cause) => failures.push(cause);
    await confirmFeedback(() => false, complete, fail);
    assert.equal(closed, 0);
    const cause = new Error("Deletion failed");
    await confirmFeedback(() => Promise.reject(cause), complete, fail);
    assert.deepEqual(failures, [cause]);
    assert.equal(closed, 0);
    await confirmFeedback(() => Promise.resolve(), complete, fail);
    assert.equal(closed, 1);
});

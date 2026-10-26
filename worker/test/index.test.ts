import assert from "node:assert/strict";
import { test } from "node:test";
import { pullsToApprove } from "../src/index.ts";

const item = (login: string, url: string) => ({ user: { login }, pull_request: { url } });

test("approves only pull requests from allowlisted authors", () => {
  assert.deepEqual(
    pullsToApprove([
      item("samzong", "https://api.github.com/repos/langgenius/dify/pulls/1"),
      item("ZhSama", "https://api.github.com/repos/samzong/demo/pulls/2"),
      item("someone", "https://api.github.com/repos/langgenius/dify/pulls/3"),
      item("micosjanf", "https://api.github.com/repos/langgenius/dify/pulls/4"),
    ]),
    ["https://api.github.com/repos/langgenius/dify/pulls/1", "https://api.github.com/repos/samzong/demo/pulls/2"],
  );
});

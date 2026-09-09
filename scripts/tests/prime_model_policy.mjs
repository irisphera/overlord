// Offline integration check against the installed Prime registry and Azure adapter.
import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [packageDirectory, configFile] = process.argv.slice(2);
assert.ok(packageDirectory && configFile, "Usage: node prime_model_policy.mjs PACKAGE_DIRECTORY MODELS_JSON");
const packageRoot = path.resolve(packageDirectory);
const localImport = (relative) => import(pathToFileURL(path.join(packageRoot, relative)).href);

// Guard against accidental network access before importing provider code.
const requests = [];
globalThis.fetch = async (_url, init) => {
  assert.ok(init && typeof init.body === "string", "Expected provider JSON request body");
  requests.push(JSON.parse(init.body));
  const completed = {
    type: "response.completed",
    response: {
      id: "resp-offline-mock",
      status: "completed",
      output: [],
      usage: {
        input_tokens: 1,
        output_tokens: 0,
        total_tokens: 1,
        input_tokens_details: { cached_tokens: 0 },
        output_tokens_details: { reasoning_tokens: 0 },
      },
    },
  };
  return new Response(`event: response.completed\ndata: ${JSON.stringify(completed)}\n\n`, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
};

const aiDir = "node_modules/@earendil-works/pi-ai/dist/";
const { getSupportedThinkingLevels, clampThinkingLevel } = await localImport(aiDir + "models.js");
const { streamSimpleAzureOpenAIResponses } = await localImport(aiDir + "providers/azure-openai-responses.js");
const { THINKING_LEVELS } = await localImport("dist/core/thinking-levels.js");
assert.deepEqual(THINKING_LEVELS, ["off", "minimal", "low", "medium", "high", "xhigh", "max"]);
assert.equal(THINKING_LEVELS.includes("none"), false);

const { ModelRegistry } = await localImport("dist/core/model-registry.js");
// Avoid loading real auth storage. Only OAuth model modifiers are used during load.
const registry = new ModelRegistry({ getOAuthProviders: () => [] }, path.resolve(configFile));
assert.equal(registry.getError(), undefined);
let model = registry.getAll().find((m) => m.provider === "azure-openai-responses" && m.id === "gpt-6-astra");
assert.ok(model, "Astra missing from actual registry");
assert.equal(model.contextWindow, 272000);
assert.equal(model.reasoning, true);
const expectedLevels = ["off", "low", "medium", "high", "xhigh", "max"];
assert.deepEqual(getSupportedThinkingLevels(model), expectedLevels);
assert.equal(model.thinkingLevelMap.off, "none");
assert.equal(model.thinkingLevelMap.minimal, null);

// Do not depend on real endpoints, deployment mappings, credentials, or environment.
model = { ...model, baseUrl: "https://mock.invalid/openai/v1", headers: undefined };
for (const key of [
  "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_RESOURCE_NAME",
  "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
]) {
  delete process.env[key];
}
for (const selected of [...expectedLevels, "minimal"]) {
  const normalized = selected === "minimal" ? "low" : selected;
  const effort = normalized === "off" ? "none" : normalized;
  assert.equal(clampThinkingLevel(model, selected), normalized);
  const stream = streamSimpleAzureOpenAIResponses(
    model,
    { messages: [{ role: "user", content: "offline test", timestamp: 0 }] },
    { apiKey: "dummy-offline-key", reasoning: selected },
  );
  const result = await stream.result();
  assert.equal(result.stopReason, "stop", JSON.stringify(result));
  const payload = requests.at(-1);
  assert.equal(payload.model, "gpt-6-astra");
  assert.equal(payload.reasoning.effort, effort);
  if (normalized === "off") {
    assert.deepEqual(payload.reasoning, { effort: "none" });
    assert.equal(payload.include, undefined);
  } else {
    assert.deepEqual(payload.reasoning, { effort, summary: "auto" });
    assert.deepEqual(payload.include, ["reasoning.encrypted_content"]);
  }
  console.log(JSON.stringify({ selected, effective: normalized, reasoning: payload.reasoning }));
}
assert.equal(requests.length, 7);
console.log("PASS: actual registry selectors and 7 Azure payloads; all fetch calls mocked");

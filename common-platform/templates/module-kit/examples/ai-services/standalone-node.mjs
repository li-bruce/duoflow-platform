/** Explicit local exercise; configure reads JSON from private stdin, never args. */
import { AIServiceManager, AIServiceError } from "../../sdk/node/ai-service.mjs";

const services = [{ id: "example.ai.analysis", title: "独立合成分析", description: "显式连接测试", capability_ids: ["example.ai.analyze"], supported_sources: ["platform", "module"] }];
try {
  if (!process.env.MODULE_AI_DATA_DIR) throw new AIServiceError("ai_private_directory_required", 500);
  const ai = new AIServiceManager({ services, dataDir: process.env.MODULE_AI_DATA_DIR });
  const action = process.argv[2] || "status", id = services[0].id;
  let output;
  if (action === "configure") {
    let raw = "";
    for await (const chunk of process.stdin) {
      raw += chunk;
      if (Buffer.byteLength(raw) > 32768) throw new AIServiceError("ai_configuration", 422);
    }
    output = ai.putConfig(id, { expected_revision: ai.getConfig(id).revision, profile: JSON.parse(raw) });
  } else if (action === "test") output = await ai.testConnection(id, ai.getConfig(id).revision);
  else if (action === "status") output = ai.getConfig(id);
  else throw new AIServiceError("ai_example_action", 422);
  process.stdout.write(JSON.stringify(output) + "\n");
} catch (error) {
  process.stderr.write(JSON.stringify({ error: error instanceof AIServiceError ? error.code : "ai_example_failed" }) + "\n");
  process.exitCode = 1;
}

/** SQLite unit of work: one connection, serial async operations, atomic business + receipt commit. */
import { DatabaseSync } from "node:sqlite";
import {
  createHash,
  randomBytes,
  createHmac,
  timingSafeEqual,
} from "node:crypto";
import { canonical, fail, requireThat } from "./runtime.mjs";

export class Store {
  constructor(filename) {
    this.db = new DatabaseSync(filename);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=4000;
      CREATE TABLE IF NOT EXISTS kit_receipts(request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, object_id TEXT NOT NULL, response TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS kit_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);`);
    this.db
      .prepare("INSERT OR IGNORE INTO kit_metadata(key,value) VALUES (?,?)")
      .run("cursor-key", randomBytes(32).toString("hex"));
    this.cursorKey = this.db
      .prepare("SELECT value FROM kit_metadata WHERE key=?")
      .get("cursor-key").value;
    this.queue = Promise.resolve();
  }
  transaction(callback) {
    const work = this.queue.then(async () => {
      this.db.exec("BEGIN IMMEDIATE");
      try {
        const result = await callback(this.db);
        this.db.exec("COMMIT");
        return result;
      } catch (error) {
        this.db.exec("ROLLBACK");
        throw error;
      }
    });
    this.queue = work.catch(() => {});
    return work;
  }
  fingerprint(request) {
    return createHash("sha256").update(canonical(request)).digest("hex");
  }
  receipt(request) {
    const saved = this.db
      .prepare("SELECT * FROM kit_receipts WHERE request_id=?")
      .get(request.request_id);
    if (saved)
      requireThat(
        saved.fingerprint === this.fingerprint(request),
        "idempotency_conflict",
        "同一请求编号的内容已经变化。",
        409,
      );
    return saved
      ? { objectId: saved.object_id, response: JSON.parse(saved.response) }
      : null;
  }
  saveReceipt(request, objectId, response) {
    this.db
      .prepare("INSERT INTO kit_receipts VALUES (?,?,?,?)")
      .run(
        request.request_id,
        this.fingerprint(request),
        objectId,
        JSON.stringify(response),
      );
  }
  cursor(binding, offset) {
    const payload = Buffer.from(canonical({ binding, offset })).toString(
      "base64url",
    );
    return `${payload}.${createHmac("sha256", this.cursorKey).update(payload).digest("base64url")}`;
  }
  offset(cursor, binding) {
    if (!cursor) return 0;
    try {
      const [payload, signature, extra] = cursor.split(".");
      const expected = Buffer.from(
        createHmac("sha256", this.cursorKey)
          .update(payload)
          .digest("base64url"),
      );
      const actual = Buffer.from(signature || "");
      if (
        extra ||
        expected.length !== actual.length ||
        !timingSafeEqual(actual, expected)
      )
        throw new Error();
      const decoded = JSON.parse(
        Buffer.from(payload, "base64url").toString("utf8"),
      );
      if (!Number.isSafeInteger(decoded.offset) || decoded.offset < 0)
        throw new Error();
      requireThat(
        canonical(decoded.binding) === canonical(binding),
        "revision_conflict",
        "目录或授权已变化，请从第一页重新读取。",
        409,
      );
      return decoded.offset;
    } catch (error) {
      if (error.code === "revision_conflict") throw error;
      throw fail("cursor_invalid", "分页游标无效。");
    }
  }
  async close() {
    await this.queue;
    this.db.close();
  }
}

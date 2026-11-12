import "server-only";
import postgres from "postgres";

type Sql = ReturnType<typeof postgres>;

// Reused across hot reloads in development, so each edit doesn't open a new pool.
const cache = globalThis as unknown as { sql?: Sql };

export function db(): Sql {
  if (!cache.sql) {
    const url = process.env.DATABASE_URL;
    if (!url) {
      throw new Error("DATABASE_URL is not set; see web/.env.example");
    }
    const local = /@(127\.0\.0\.1|localhost)[:/]/.test(url);
    cache.sql = postgres(url, {
      ssl: local ? false : "require",
      // Supabase's transaction pooler (port 6543) can't hold prepared statements.
      prepare: false,
      // Supavisor before 2.10 hangs on pipelined queries.
      max_pipeline: 1,
      max: 3,
      idle_timeout: 20,
      connect_timeout: 10,
      query_timeout: 15,
      connection: { application_name: "nhl-forecast-web" },
    });
  }
  return cache.sql;
}

import "server-only";
import { cacheLife } from "next/cache";
import { db } from "@/lib/db";

export type SiteStatus = {
  updatedAt: string | null;
  upcomingGames: number;
};

export async function getSiteStatus(): Promise<SiteStatus> {
  "use cache";
  cacheLife("minutes");
  const [row] = await db()<{ updated_at: Date | null; upcoming: number }[]>`
    select
      (select max(finished_at) from job_runs
       where job = 'daily' and status in ('ok', 'validation_failed')) as updated_at,
      (select count(*)::int from games
       where game_type in (2, 3)
         and start_time_utc > now() and start_time_utc <= now() + interval '36 hours') as upcoming
  `;
  return {
    updatedAt: row.updated_at ? row.updated_at.toISOString() : null,
    upcomingGames: row.upcoming,
  };
}

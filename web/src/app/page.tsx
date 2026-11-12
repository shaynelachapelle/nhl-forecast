import { getSiteStatus } from "@/lib/data";

export default async function Home() {
  const status = await getSiteStatus();
  const updated = status.updatedAt
    ? new Intl.DateTimeFormat("en-US", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "UTC",
      }).format(new Date(status.updatedAt))
    : null;

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-12">
      <p className="font-num text-sm font-semibold tracking-wider text-muted-foreground uppercase">
        NHL Forecast
      </p>
      <h1 className="font-heading text-4xl font-semibold text-balance">Who wins tonight</h1>
      <p className="text-muted-foreground">
        <span className="font-num font-semibold text-foreground tabular-nums">
          {status.upcomingGames}
        </span>{" "}
        games in the next 36 hours.{" "}
        {updated ? (
          <>
            Updated <time dateTime={status.updatedAt ?? undefined}>{updated} UTC</time>.
          </>
        ) : (
          "No completed run yet."
        )}
      </p>
    </main>
  );
}

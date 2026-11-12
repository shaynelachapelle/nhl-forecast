-- One row per job run, so "last updated" and stalled jobs can be read from the
-- database. predictions.run_id matches run_id for runs logged from here on.
create table public.job_runs (
    run_id            uuid        primary key,
    job               text        not null,  -- e.g. 'daily'
    started_at        timestamptz not null default now(),
    finished_at       timestamptz,
    status            text        not null default 'running'
                      check (status in ('running', 'ok', 'validation_failed', 'failed')),
    prediction_counts jsonb,                 -- per model: written, unchanged, already_started
    error             text,
    git_commit        text,
    check ((status = 'running') = (finished_at is null))
);

alter table public.job_runs enable row level security;
revoke all on public.job_runs from anon, authenticated;

-- Helpers shared by later migrations, plus the raw response store.

-- Raises on any write it is attached to. Used to make tables append-only.
create function public.forbid_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception '% on %.% is not allowed: table is append-only',
        tg_op, tg_table_schema, tg_table_name;
end;
$$;

-- Keeps updated_at honest on upserts without the caller having to set it.
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

-- Every HTTP response, stored verbatim. This is the source of truth:
-- every derived table can be rebuilt by re-parsing these rows.
create table public.raw_api_responses (
    id             bigint generated always as identity primary key,
    source         text        not null,  -- 'nhl_web', 'nhl_stats', ...
    endpoint       text        not null,  -- path template, e.g. '/v1/schedule/{date}'
    url            text        not null,  -- full URL actually requested
    params         jsonb       not null default '{}'::jsonb,
    fetched_at     timestamptz not null default now(),
    http_status    smallint    not null,
    payload        jsonb       not null,
    payload_sha256 text        not null,
    -- Refetching identical content is a no-op (ON CONFLICT DO NOTHING);
    -- changed content gets a new row, so earlier versions are kept.
    constraint raw_api_responses_url_payload_key unique (url, payload_sha256)
);

create index raw_api_responses_url_fetched_idx
    on public.raw_api_responses (url, fetched_at desc);

create trigger raw_api_responses_append_only
    before update or delete on public.raw_api_responses
    for each row execute function public.forbid_mutation();

create trigger raw_api_responses_no_truncate
    before truncate on public.raw_api_responses
    for each statement execute function public.forbid_mutation();

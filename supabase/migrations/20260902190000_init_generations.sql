-- MangaRelief — Supabase schema (phase 1)
--
-- One table does three jobs: it is the state a client polls, the retention
-- ledger /api/internal/cleanup walks, and the usage log that phase 3 will turn
-- into per-user quota. Only the service-role key touches it; row level security
-- is on with no public policy, so the anon key sees nothing.

create table if not exists public.generations (
    id            text primary key,
    created_at    timestamptz not null default now(),
    user_id       uuid references auth.users(id) on delete set null,
    ip_hash       text,                       -- salted hash, never the address
    mode          text not null,
    params        jsonb not null default '{}'::jsonb,
    status        text not null default 'queued',
    progress      int  not null default 0,
    message       text default '',
    duration_s    double precision,
    error         text,
    artifacts     jsonb not null default '[]'::jsonb,
    expires_at    timestamptz,
    downloaded_at timestamptz
);

-- Cleanup scans by expiry; quota counts by ip_hash (and later user_id) over time.
create index if not exists generations_expires_idx  on public.generations (expires_at)
    where status <> 'expired';
create index if not exists generations_ip_time_idx  on public.generations (ip_hash, created_at desc);
create index if not exists generations_user_time_idx on public.generations (user_id, created_at desc);

alter table public.generations enable row level security;
-- No policy on purpose: the API is the only client, and it uses the service key.

-- Storage bucket for the produced files. Private: downloads go through the API,
-- which is what enforces expiry.
insert into storage.buckets (id, name, public)
values ('generations', 'generations', false)
on conflict (id) do nothing;

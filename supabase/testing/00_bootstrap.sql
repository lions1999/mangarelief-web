-- Quel pezzo di Supabase che un Postgres nudo non ha.
--
-- Le migrazioni sono scritte per Supabase e ne danno per scontate due cose:
-- lo schema `auth` (la tabella utenti a cui `user_id` fa riferimento) e lo
-- schema `storage` (dove si dichiara il bucket). Su un Postgres qualunque non
-- esistono, e la prima migrazione fallirebbe alla riga della chiave esterna.
--
-- Questo file crea il minimo indispensabile perche' le migrazioni vere girino
-- senza essere modificate. E' importante che restino non modificate: sono le
-- stesse che vengono incollate in produzione, e una copia "adattata per i
-- test" proverebbe qualcos'altro.
--
-- Quello che qui NON si riproduce, e va ricordato quando si legge un esito
-- verde: GoTrue (l'autenticazione vera), le policy RLS come sono configurate
-- nel progetto, e lo Storage. Qui c'e' PostgREST sopra Postgres, che e' lo
-- stesso software che serve le nostre query — non l'intero Supabase.

create schema if not exists auth;
create table if not exists auth.users (
    id    uuid primary key,
    email text
);

create schema if not exists storage;
create table if not exists storage.buckets (
    id     text primary key,
    name   text,
    public boolean default false
);

-- I ruoli con cui PostgREST si collega, come li usa Supabase:
--   authenticator  entra e basta, non ha privilegi propri
--   service_role   quello con cui parla l'API, e che scavalca RLS
--   anon           quello che tocca a chi non presenta un token
-- Il punto interessante e' proprio service_role: la tabella ha RLS attivo
-- senza policy, quindi senza BYPASSRLS ogni query tornerebbe vuota — e una
-- riga vuota si legge come "nessun dato", non come "non ti e' permesso".
do $$
begin
    if not exists (select from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select from pg_roles where rolname = 'service_role') then
        create role service_role nologin bypassrls;
    end if;
    if not exists (select from pg_roles where rolname = 'authenticator') then
        create role authenticator login password 'authenticator';
    end if;
end
$$;

grant anon, service_role to authenticator;
grant usage on schema public to anon, service_role;

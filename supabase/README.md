# Supabase

`migrations/` holds the schema as versioned migrations, which is the layout the
Supabase GitHub integration and `supabase db push` both expect. Every statement
is idempotent (`if not exists` / `on conflict do nothing`), so applying it over a
database where the SQL was already pasted into the editor by hand is a no-op.

What it creates:

- `public.generations` — job state, retention ledger and usage log in one table
- three indexes: expiry sweep, per-IP rate counting, per-user quota (phase 3)
- row level security **on with no policy**: the API is the only client and it
  uses the service-role key, so the anon key sees nothing
- the private `generations` storage bucket

## Checking it landed

```sql
select table_name from information_schema.tables
 where table_schema = 'public' and table_name = 'generations';

select indexname from pg_indexes
 where tablename = 'generations';

select id, public from storage.buckets where id = 'generations';
```

Expected: one table, three `generations_*` indexes plus the primary key, and one
bucket with `public = false`.

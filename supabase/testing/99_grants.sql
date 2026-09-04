-- Permessi sulle tabelle create dalle migrazioni. Va dopo, non prima: prima
-- non esistono. Su Supabase questi grant li fa il progetto per conto suo.
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
-- anon resta senza privilegi di proposito: e' cosi' che si vede che RLS e i
-- permessi stanno facendo il loro mestiere invece che essere decorativi.

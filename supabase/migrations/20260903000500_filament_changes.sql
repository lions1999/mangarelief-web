-- The print plan: at which Z to swap filament, and to which colour.
--
-- The 3MF carries this inside the file, but anyone printing the plain STL has
-- nothing to go on without it, and the desktop app has always shown it. Kept
-- on the row so the result page can show it without re-deriving the geometry.

alter table public.generations
    add column if not exists filament_changes jsonb not null default '[]'::jsonb;

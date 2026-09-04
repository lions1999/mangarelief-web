-- Quale browser ha chiesto una generazione anonima.
--
-- Serve a due cose. Contare le prove gratuite per dispositivo invece che per
-- IP: sotto CGNAT migliaia di persone condividono un indirizzo, e limitare per
-- IP punisce chi non ha fatto nulla. E collegare le prove gia' fatte a un
-- account al primo accesso, cosi' chi si registra dopo aver provato non
-- ricomincia da capo con il totale pieno.
--
-- Casuale, generato dal browser, senza alcun dato personale: identifica una
-- installazione, non una persona.

alter table public.generations
    add column if not exists device_id text;

-- La quota conta le righe di un dispositivo in una finestra temporale.
create index if not exists generations_device_time_idx
    on public.generations (device_id, created_at desc)
    where device_id is not null;

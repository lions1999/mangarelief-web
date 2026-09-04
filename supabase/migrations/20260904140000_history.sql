-- La cronologia: cosa resta di una generazione quando i file sono scaduti.
--
-- I file vivi sono la risorsa scarsa (STL + 3MF ≈ 9 MB a generazione, contro
-- 1 GB di bucket), quindi la cronologia non li tiene in vita piu' a lungo.
-- Conserva invece due cose molto piu' piccole:
--
--   preview_key  la miniatura del mockup, ~7 KB: quattro colori piatti si
--                comprimono quasi a nulla. Resta per sempre — e' cio' che
--                rende riconoscibile una voce scaduta.
--   source_key   l'immagine sorgente ridotta a 800px (il tetto del piano
--                gratuito), ~110 KB. Serve a rigenerare con un clic, e si
--                conserva solo per le ultime generazioni di ogni account:
--                a 5.000 voci occuperebbe da sola meta' del bucket.
--
-- Vivono sotto il prefisso `history/<id>/`, non `<id>/`, perche' la pulizia a
-- scadenza cancella l'intera cartella `<id>` e porterebbe via anche loro.
--
-- hidden_at e' il cestino, e non e' una cancellazione: la riga *e'* il registro
-- della quota, quindi eliminarla darebbe a chiunque il modo di azzerare il
-- proprio contatore (genera, scarica, cancella, rigenera). Si nasconde dalla
-- cronologia, i file se ne vanno davvero, la riga resta a essere contata.

alter table public.generations
    add column if not exists image_name  text,
    add column if not exists preview_key text,
    add column if not exists source_key  text,
    add column if not exists hidden_at   timestamptz;

-- La cronologia di un account, dalla piu' recente: l'indice esistente su
-- (user_id, created_at desc) la serve gia'; questo esclude le nascoste.
create index if not exists generations_history_idx
    on public.generations (user_id, created_at desc)
    where user_id is not null and hidden_at is null;

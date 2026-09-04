"""Il contratto dell'archivio: una lista di asserzioni, due implementazioni.

`SqliteStore` e `SupabaseStore` devono comportarsi allo stesso modo, e finora
niente lo imponeva: il contratto fra le due stava nella mia testa, e ogni
modifica lo riscriveva due volte sperando che coincidessero. La suite gira in
locale su SQLite, quindi l'implementazione che serve il sito vero e' quella mai
provata — e le prove che la riguardano controllano l'URL che costruisco io, con
il mio stesso codice come giudice. Se la mia idea di PostgREST e' sbagliata,
sono sbagliati insieme il codice e la prova, e vanno d'accordo.

Qui le asserzioni sono scritte una volta sola e usano **solo l'interfaccia
pubblica**: niente SQL, niente URL, niente dettagli di nessuna delle due. Chi
la esegue passa un archivio gia' pronto. Oggi la esegue la suite con SQLite;
con un PostgREST vero accanto, la stessa lista misura quello che oggi e' solo
una convinzione.

Due regole per chi ci aggiunge qualcosa:

- **Nessuna asserzione puo' presumere un database vuoto.** Contro Supabase gira
  su una tabella che contiene gia' righe vere: ogni prova si crea le sue, con
  identificativi propri, e guarda solo quelle.
- **Niente parita' di `created_at`.** L'ordine fra righe con lo stesso istante
  non e' definito in Postgres, mentre in SQLite segue il rowid: una prova che
  ci si appoggiasse passerebbe qui e sarebbe una moneta lanciata li'.
- **Gli identificativi di account arrivano da `nuovo_utente`, mai inventati.**
  Su SQLite `user_id` e' testo e accetta qualunque cosa; su Postgres e' un
  `uuid` con una chiave esterna verso la tabella degli utenti. Le prime righe
  scritte qui inventavano stringhe: passavano su SQLite e su Postgres
  rispondevano `invalid input syntax for type uuid`, poi `violates foreign key
  constraint`. E' la divergenza che questo file esiste per trovare — l'ha
  trovata su se' stesso al primo giro.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.store import COLUMNS, iso, parse_iso


def _ora() -> datetime:
    return datetime.now(timezone.utc)


class Fabbrica:
    """Righe complete e sempre diverse.

    Complete perche' le due implementazioni riempiono diversamente i campi
    mancanti — SQLite mette None, Postgres i propri default — e un contratto
    che dipendesse da quello starebbe misurando il caso, non l'accordo.
    """

    def __init__(self, nuovo_utente: Optional[Callable[[], str]] = None):
        self.marchio = uuid.uuid4().hex[:12]
        self.nuovo_utente = nuovo_utente or (lambda: str(uuid.uuid4()))

    def id(self, etichetta: str = "") -> str:
        return f"{self.marchio}{etichetta}{uuid.uuid4().hex}"[:32]

    def utente(self) -> str:
        """Un identificativo di account valido *e* registrato.

        Chi esegue il contratto decide cosa voglia dire registrato: su SQLite
        niente, su un Postgres vero una riga in auth.users, perche' altrimenti
        la chiave esterna rifiuta l'inserimento.
        """
        return self.nuovo_utente()

    def riga(self, **modifiche) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "id": self.id(),
            "created_at": iso(_ora()),
            "user_id": None,
            "ip_hash": None,
            "device_id": None,
            "mode": "standard",
            "params": {"requested": {"mode": "standard"}, "resolved": {"color_mode": 4}},
            "status": "done",
            "progress": 100,
            "message": "Completed",
            "duration_s": 1.25,
            "error": None,
            "artifacts": [{"kind": "stl", "filename": "a.stl", "key": "k/a.stl", "bytes": 7}],
            "filament_changes": [{"z": 1.2, "color": None}],
            "expires_at": iso(_ora() + timedelta(hours=48)),
            "downloaded_at": None,
            "image_name": None,
            "preview_key": None,
            "source_key": None,
            "hidden_at": None,
        }
        base.update(modifiche)
        assert set(base) == set(COLUMNS), set(base).symmetric_difference(COLUMNS)
        return base


def esercita(store, check: Callable[..., None], dove: str = "",
             nuovo_utente: Optional[Callable[[], str]] = None) -> None:
    """Esegue il contratto contro `store`, riportando con `check`.

    `nuovo_utente` restituisce un identificativo di account utilizzabile su
    quell'archivio, registrandolo se serve. Senza, se ne genera uno a caso: va
    bene dove `user_id` e' solo testo, non dove e' un uuid con una chiave
    esterna dietro.
    """
    f = Fabbrica(nuovo_utente)
    p = f"[{dove}] " if dove else ""
    finestra = _ora() - timedelta(hours=24)

    # ---------------------------------------------------------- andata e ritorno
    r = f.riga(user_id=f.utente(), ip_hash="ip-" + f.marchio, device_id="dev-" + f.marchio,
               image_name="Roger — pagina 12.png")
    store.insert(r)
    letta = store.get(r["id"])
    check(p + "quel che si scrive si rilegge", letta is not None, letta)
    mancanti = [c for c in COLUMNS if c not in (letta or {})]
    check(p + "la riga riletta ha tutte le colonne", not mancanti, mancanti)

    # I campi jsonb sono la trappola: SQLite li serializza a mano e li rilegge
    # con json.loads, PostgREST li restituisce gia' strutturati. Un chiamante
    # che ricevesse una stringa dove si aspetta un dizionario si romperebbe
    # solo in produzione.
    check(p + "params torna strutturato, non come testo",
          isinstance(letta["params"], dict)
          and letta["params"]["resolved"]["color_mode"] == 4, type(letta["params"]).__name__)
    check(p + "artifacts torna come lista di dizionari",
          isinstance(letta["artifacts"], list) and letta["artifacts"][0]["bytes"] == 7,
          type(letta["artifacts"]).__name__)
    check(p + "filament_changes torna come lista",
          isinstance(letta["filament_changes"], list)
          and letta["filament_changes"][0]["z"] == 1.2, letta["filament_changes"])
    check(p + "il testo non ASCII sopravvive al giro",
          letta["image_name"] == "Roger — pagina 12.png", letta["image_name"])
    check(p + "le date tornano interpretabili e con fuso",
          parse_iso(letta["created_at"]) is not None
          and parse_iso(letta["created_at"]).tzinfo is not None, letta["created_at"])
    check(p + "un id che non esiste da' None, non un errore",
          store.get("non-esiste-" + f.marchio) is None)

    # ------------------------------------------------------------- aggiornamento
    store.update(r["id"], {"progress": 42, "message": "A meta'"})
    dopo = store.get(r["id"])
    check(p + "update cambia i campi indicati", dopo["progress"] == 42, dopo["progress"])
    check(p + "e lascia stare gli altri",
          dopo["mode"] == "standard" and dopo["artifacts"][0]["bytes"] == 7, dopo["mode"])
    store.update(r["id"], {"source_key": "history/x/source.webp"})
    store.update(r["id"], {"source_key": None})
    check(p + "update a None svuota il campo davvero",
          store.get(r["id"])["source_key"] is None, store.get(r["id"])["source_key"])

    # ------------------------------------------------------------------- quota
    ip = "ipq-" + f.marchio
    vecchia = f.riga(ip_hash=ip, created_at=iso(_ora() - timedelta(hours=40)))
    media = f.riga(ip_hash=ip, created_at=iso(_ora() - timedelta(hours=5)))
    recente = f.riga(ip_hash=ip, created_at=iso(_ora() - timedelta(hours=1)))
    for riga in (vecchia, media, recente):
        store.insert(riga)

    quante, piu_vecchia = store.usage_since("ip_hash", ip, finestra)
    check(p + "si contano solo le righe dentro la finestra", quante == 2, quante)
    check(p + "e si riporta la piu' vecchia fra quelle, non la prima trovata",
          parse_iso(piu_vecchia) == parse_iso(media["created_at"]),
          (piu_vecchia, media["created_at"]))
    check(p + "un'identita' senza righe da' zero e nessuna data",
          store.usage_since("ip_hash", "mai-visto-" + f.marchio, finestra) == (0, None))
    check(p + "count_recent dice lo stesso numero",
          store.count_recent(ip, finestra) == quante, store.count_recent(ip, finestra))

    campo_rifiutato = None
    try:
        store.usage_since("params", "x", finestra)
    except Exception as exc:  # noqa: BLE001 - e' proprio quello che si misura
        campo_rifiutato = type(exc).__name__
    check(p + "un campo non ammesso non arriva alla query", campo_rifiutato == "ValueError",
          campo_rifiutato)

    # -------------------------------------------------- collegare un dispositivo
    # La piu' delicata di tutte: e' una UPDATE con un filtro. Se il filtro non
    # filtrasse, attribuirebbe all'account di chi si registra le generazioni
    # anonime di chiunque altro abbia usato quel dispositivo.
    disp = "devl-" + f.marchio
    altro_disp = "devx-" + f.marchio
    mio = f.utente()
    tuo = f.utente()

    anonima = f.riga(device_id=disp, created_at=iso(_ora() - timedelta(hours=2)))
    gia_di_altri = f.riga(device_id=disp, user_id=tuo, created_at=iso(_ora() - timedelta(hours=3)))
    troppo_vecchia = f.riga(device_id=disp, created_at=iso(_ora() - timedelta(hours=40)))
    di_un_altro_browser = f.riga(device_id=altro_disp, created_at=iso(_ora() - timedelta(hours=2)))
    for riga in (anonima, gia_di_altri, troppo_vecchia, di_un_altro_browser):
        store.insert(riga)

    spostate = store.link_device(disp, mio, finestra)
    check(p + "collegare sposta solo le anonime recenti di quel dispositivo",
          spostate == 1, spostate)
    check(p + "la riga anonima ora e' dell'account", store.get(anonima["id"])["user_id"] == mio)
    check(p + "una riga gia' di qualcun altro non viene rubata",
          store.get(gia_di_altri["id"])["user_id"] == tuo,
          store.get(gia_di_altri["id"])["user_id"])
    check(p + "una riga fuori finestra resta anonima",
          store.get(troppo_vecchia["id"])["user_id"] is None)
    check(p + "un altro browser non viene toccato",
          store.get(di_un_altro_browser["id"])["user_id"] is None)
    check(p + "collegare due volte non conta due volte",
          store.link_device(disp, mio, finestra) == 0)

    # ----------------------------------------------------------------- scadenze
    scaduta = f.riga(expires_at=iso(_ora() - timedelta(hours=1)))
    gia_marcata = f.riga(status="expired", expires_at=iso(_ora() - timedelta(hours=1)))
    viva = f.riga(expires_at=iso(_ora() + timedelta(hours=10)))
    for riga in (scaduta, gia_marcata, viva):
        store.insert(riga)

    scadute = {x["id"] for x in store.list_expired(_ora(), limit=500)}
    check(p + "le scadute si trovano", scaduta["id"] in scadute)
    check(p + "quelle gia' marcate non si ripresentano", gia_marcata["id"] not in scadute)
    check(p + "quelle ancora vive restano fuori", viva["id"] not in scadute)
    check(p + "il limite viene rispettato", len(store.list_expired(_ora(), limit=1)) <= 1)

    # --------------------------------------------------------------- cronologia
    tizio = f.utente()
    # Istanti distinti di proposito: a parita' di created_at l'ordine non e'
    # definito in Postgres, e una prova che ci contasse sarebbe una moneta.
    voci = [f.riga(user_id=tizio, source_key=f"history/{i}/source.webp",
                   preview_key=f"history/{i}/preview.webp",
                   created_at=iso(_ora() - timedelta(minutes=10 * (5 - i))))
            for i in range(5)]
    nascosta = f.riga(user_id=tizio, hidden_at=iso(_ora()),
                      created_at=iso(_ora() - timedelta(minutes=1)))
    for riga in voci + [nascosta]:
        store.insert(riga)

    elenco = store.history(tizio, 60)
    ids = [x["id"] for x in elenco]
    check(p + "la cronologia contiene solo le righe di quell'account",
          {x["user_id"] for x in elenco} == {tizio}, {x["user_id"] for x in elenco})
    check(p + "dalla piu' recente alla piu' vecchia",
          ids == [v["id"] for v in reversed(voci)], ids)
    check(p + "le nascoste non compaiono", nascosta["id"] not in ids)
    check(p + "il limite vale anche qui", len(store.history(tizio, 2)) == 2)

    # -------------------------------------------------------------- la potatura
    oltre = {x["id"] for x in store.sources_beyond(tizio, 2)}
    check(p + "oltre il tetto ci sono le piu' vecchie, non le recenti",
          oltre == {v["id"] for v in voci[:3]}, oltre)
    check(p + "quel che torna porta la chiave da cancellare",
          all(x.get("source_key") for x in store.sources_beyond(tizio, 2)))
    check(p + "con tetto zero c'e' tutto quel che ha una sorgente",
          {x["id"] for x in store.sources_beyond(tizio, 0)} == {v["id"] for v in voci},
          len(store.sources_beyond(tizio, 0)))
    check(p + "con un tetto piu' alto delle voci non si pota niente",
          store.sources_beyond(tizio, 50) == [])
    store.update(voci[0]["id"], {"source_key": None})
    check(p + "una voce gia' potata non si ripresenta",
          voci[0]["id"] not in {x["id"] for x in store.sources_beyond(tizio, 0)})

    # ------------------------------------------------------------------ schema
    check(p + "lo schema e' quello che il codice si aspetta",
          store.schema_problem() is None, store.schema_problem())

"""La barra dell'account non deve tagliare niente, in nessuno dei suoi stati.

Sta nel repo e non fra i file usa-e-getta perche' questo difetto e' gia'
ricomparso due volte, in due punti diversi, per la stessa ragione: in quella
barra convivono testi di lunghezza ignota (un indirizzo email) e comandi che
devono restare leggibili, e ogni volta che si aggiunge qualcosa alla riga il
pezzo elastico torna a cedere. La prima volta si leggeva "sig..." al posto di
"sign out"; la seconda, aggiunto il collegamento alla cronologia, e' tornato a
troncarsi l'indirizzo.

Non e' un controllo di aspetto: misura il taglio. Un elemento e' tagliato
quando il testo che contiene e' piu' largo dello spazio che ha
(`scrollWidth > clientWidth`), ed e' una cosa che si puo' chiedere al browser
invece che a un occhio.

    npm run build && npx vite preview --port 4173     # in un terminale
    python web/tests/account_bar.py                   # nell'altro
"""

import json
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:4173/"
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

# Lungo di proposito: il caso vero che ha rotto la barra due volte.
EMAIL = "davideleoni99@gmail.com"

SESSIONE = {"access_token": "at", "refresh_token": "rt", "expires_at": 9999999999,
            "email": EMAIL, "user_id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"}

LIMITI = {"anon_generations": 2, "user_generations": 5, "window_h": 24,
          "retention_h": 48, "post_download_h": 24, "max_upload_mb": 12,
          "max_dim_mm": 200.0, "max_res_cap": 800, "modes": ["standard", "spot_color"]}

fails = []


def check(n, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + n + ((" | " + str(extra)) if extra else ""))
    if not ok:
        fails.append(n)


def json_route(payload):
    return lambda r: r.fulfill(status=200, content_type="application/json",
                               body=json.dumps(payload))


def tagliati(page, selettore):
    """Gli elementi il cui testo non ci sta nello spazio che hanno."""
    return page.eval_on_selector_all(selettore, """els => els
        .filter(e => e.scrollWidth > e.clientWidth + 1)
        .map(e => e.textContent.trim())""")


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=CHROMIUM)

    def apri(quota, sessione=None, larghezza=1600):
        page = b.new_page(viewport={"width": larghezza, "height": 900})
        avvio = ["localStorage.setItem('mangarelief.welcome', '1');"]
        if sessione:
            avvio.append("localStorage.setItem('mangarelief.session', "
                         f"{json.dumps(json.dumps(sessione))});")
        page.add_init_script("".join(avvio))
        page.route("**/api/limits", json_route(LIMITI))
        page.route("**/api/quota", json_route(quota))
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(400)
        return page

    stati = [
        ("anonimo", {"plan": "anonymous", "limit": 2, "used": 0, "remaining": 2,
                     "reset_at": None}, None),
        ("registrato", {"plan": "registered", "limit": 5, "used": 2, "remaining": 3,
                        "reset_at": None}, SESSIONE),
        ("illimitato", {"plan": "unlimited", "limit": None, "used": 9, "remaining": None,
                        "reset_at": None}, SESSIONE),
        ("esaurito", {"plan": "registered", "limit": 5, "used": 5, "remaining": 0,
                      "reset_at": "2030-01-01T00:00:00+00:00"}, SESSIONE),
    ]

    for nome, quota, sessione in stati:
        page = apri(quota, sessione)
        barra = page.locator(".account")
        testo = barra.inner_text()

        check(f"{nome}: la barra c'e'", barra.count() == 1)
        check(f"{nome}: l'etichetta non cambia mai", "Generations left" in testo, testo[:60])

        rovinati = tagliati(page, ".account *")
        check(f"{nome}: niente testo tagliato", not rovinati, rovinati)

        # I comandi si leggono per intero: sono la ragione per cui la barra
        # esiste, e sono i primi a essere sacrificati quando si stringe.
        for atteso in (["sign in"] if not sessione else ["your generations", "sign out"]):
            trovato = page.locator(".account-links button", has_text=atteso)
            check(f"{nome}: «{atteso}» si legge per intero",
                  trovato.count() == 1 and trovato.first.inner_text().strip() == atteso,
                  trovato.first.inner_text() if trovato.count() else "assente")

        if sessione:
            indirizzo = page.locator(".account-who")
            check(f"{nome}: l'indirizzo si legge per intero",
                  indirizzo.inner_text().strip() == EMAIL, indirizzo.inner_text())
            check(f"{nome}: e ha una riga tutta sua",
                  page.eval_on_selector(".account-who",
                                        "e => getComputedStyle(e).display") == "block")

        page.screenshot(path=f"/tmp/lab/barra_{nome}.png",
                        clip={"x": 0, "y": 0, "width": 372, "height": 200})
        page.close()

    # Su uno schermo stretto la barra e' larga quanto tutto: se qualcosa deve
    # cedere, che ceda l'indirizzo (che ha il title) e mai un comando.
    page = apri(stati[1][1], SESSIONE, larghezza=390)
    comandi = tagliati(page, ".account-links button")
    check("telefono: i comandi restano interi anche a 390px", not comandi, comandi)
    page.close()

    b.close()

print("\n" + ("TUTTO OK" if not fails else "FALLITI: " + ", ".join(fails)))
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WettBot – Live-Quoten-Scanner
=============================
Holt aktuelle Quoten von vielen Buchmachern (The Odds API), berechnet faire
Wahrscheinlichkeiten (Pinnacle bzw. Markt-Konsens ohne Marge) und meldet:
  * Value Bets   (Quote bei deinem Buchmacher > faire Quote)
  * Surebets     (Arbitrage über mehrere Buchmacher)
per Konsole, Telegram und HTML-Bericht (bericht.html).

Nur Python-Standardbibliothek – keine Installation nötig.

Aufruf:
  python wettbot.py            Dauerbetrieb (scannt automatisch, Credits werden eingeteilt)
  python wettbot.py --einmal   Ein Scan, dann Ende
  python wettbot.py --demo     Test mit Fantasie-Daten (kein API-Key nötig)
  python wettbot.py --sportarten   Aktive Sportarten + Keys anzeigen (kostet 0 Credits)
  python wettbot.py --telegram-test   Testnachricht an Telegram senden
"""
import argparse
import csv
import html
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASIS = os.path.dirname(os.path.abspath(__file__))
API = "https://api.the-odds-api.com/v4"
STATE_DATEI = os.path.join(BASIS, "wettbot_state.json")
CSV_DATEI = os.path.join(BASIS, "signale.csv")
BERICHT = os.path.join(BASIS, "bericht.html")

STANDARD = {
    "ODDS_API_KEY": "",
    "TELEGRAM_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "SPORTARTEN": "soccer_germany_bundesliga,soccer_epl,soccer_uefa_champs_league",
    "MAERKTE": "h2h",
    "REGION": "eu",
    "SHARP_BUCHMACHER": "pinnacle",
    "ZIEL_BUCHMACHER": "tipico_de,winamax_de",
    "MIN_VALUE_PROZENT": "2",
    "MAX_VALUE_PROZENT": "25",
    "MIN_QUOTE": "1.30",
    "MAX_QUOTE": "6.00",
    "MIN_BUECHER_KONSENS": "5",
    "WETTSTEUER_PROZENT": "0",
    "BANKROLL": "200",
    "KELLY_ANTEIL": "0.25",
    "MAX_EINSATZ_PROZENT": "3",
    "VORLAUF_STUNDEN": "72",
    "LIVE_SPIELE": "nein",
    "MAX_QUOTEN_ALTER_MIN": "0",
    "INTERVALL_MIN": "0",
    "MIN_INTERVALL_MIN": "20",
    "CREDIT_RESERVE": "20",
    "BERICHT_OEFFNEN": "ja",
}

NAMEN = {"Draw": "Unentschieden", "Over": "Über", "Under": "Unter"}
SPORT_ICON = {"soccer": "⚽", "basketball": "🏀", "tennis": "🎾", "icehockey": "🏒",
              "americanfootball": "🏈", "baseball": "⚾", "mma": "🥊", "boxing": "🥊",
              "darts": "🎯", "cricket": "🏏", "rugbyleague": "🏉", "rugbyunion": "🏉",
              "handball": "🤾", "aussierules": "🏉", "golf": "⛳"}
# Wettbörsen nehmen Provision → nicht für Surebets/Bestquoten verwenden
BOERSEN = {"betfair_ex_eu", "betfair_ex_uk", "matchbook", "smarkets"}
WOCHENTAG = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# ───────────────────────── Konfiguration ─────────────────────────
def lade_config(pfad):
    cfg = dict(STANDARD)
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.strip()
                if not zeile or zeile.startswith("#") or "=" not in zeile:
                    continue
                k, v = zeile.split("=", 1)
                cfg[k.strip().upper()] = v.split(" #")[0].strip().strip('"').strip("'")
    else:
        print(f"⚠  {pfad} nicht gefunden – nutze Standardwerte.")
    c = {}
    for k, v in cfg.items():
        c[k] = v
    f = lambda k: float(str(cfg[k]).replace(",", "."))
    liste = lambda k: [x.strip() for x in cfg[k].split(",") if x.strip()]
    c.update(
        sportarten=liste("SPORTARTEN"), maerkte=liste("MAERKTE"), sharp=cfg["SHARP_BUCHMACHER"].strip(),
        ziel=liste("ZIEL_BUCHMACHER"), min_edge=f("MIN_VALUE_PROZENT") / 100,
        max_edge=f("MAX_VALUE_PROZENT") / 100, min_q=f("MIN_QUOTE"), max_q=f("MAX_QUOTE"),
        min_buecher=int(f("MIN_BUECHER_KONSENS")), steuer=f("WETTSTEUER_PROZENT") / 100,
        bankroll=f("BANKROLL"), kelly=f("KELLY_ANTEIL"), max_einsatz=f("MAX_EINSATZ_PROZENT") / 100,
        vorlauf=f("VORLAUF_STUNDEN"), live=cfg["LIVE_SPIELE"].lower() in ("ja", "yes", "1", "true"),
        max_alter=f("MAX_QUOTEN_ALTER_MIN"), intervall=f("INTERVALL_MIN"),
        min_intervall=f("MIN_INTERVALL_MIN"), reserve=int(f("CREDIT_RESERVE")),
        oeffnen=cfg["BERICHT_OEFFNEN"].lower() in ("ja", "yes", "1", "true"),
    )
    return c


# ───────────────────────── API ─────────────────────────
class ApiFehler(Exception):
    pass


def hole(pfad, params):
    url = f"{API}{pfad}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "WettBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            daten = json.loads(r.read().decode("utf-8"))
            return daten, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        if e.code == 401:
            if "OUT_OF_USAGE_CREDITS" in text or "quota" in text.lower():
                raise ApiFehler("Monats-Credits aufgebraucht. Reset am 1. des Monats.")
            raise ApiFehler("API-Key ungültig. Prüfe ODDS_API_KEY in config.env.")
        if e.code == 422:
            raise ApiFehler(f"Ungültige Anfrage ({pfad}): {text[:200]}")
        if e.code == 429:
            raise ApiFehler("Zu viele Anfragen – kurz warten.")
        raise ApiFehler(f"HTTP {e.code}: {text[:200]}")
    except urllib.error.URLError as e:
        raise ApiFehler(f"Keine Verbindung: {e.reason}")


def aktive_sportarten(key):
    daten, _ = hole("/sports/", {"apiKey": key})  # kostet 0 Credits
    return daten


def loese_sportarten(cfg):
    """Erlaubt Präfixe wie 'tennis_atp' → alle gerade aktiven ATP-Turniere."""
    aktiv = [s["key"] for s in aktive_sportarten(cfg["ODDS_API_KEY"]) if s.get("active")]
    ergebnis = []
    for eintrag in cfg["sportarten"]:
        treffer = [k for k in aktiv if k == eintrag] or [k for k in aktiv if k.startswith(eintrag)]
        if not treffer:
            print(f"   (übersprungen: {eintrag} – gerade keine Spiele/Saison)")
        ergebnis += [t for t in treffer if t not in ergebnis]
    return ergebnis


def hole_quoten(cfg, sport):
    return hole(f"/sports/{sport}/odds", {
        "apiKey": cfg["ODDS_API_KEY"], "regions": cfg["REGION"],
        "markets": ",".join(cfg["maerkte"]), "oddsFormat": "decimal", "dateFormat": "iso",
    })


# ───────────────────────── Mathematik ─────────────────────────
def ohne_marge(quoten):
    """Proportionale Margen-Entfernung: p_i = (1/q_i) / Σ(1/q)."""
    inv = [1 / q for q in quoten]
    s = sum(inv)
    return [x / s for x in inv], s - 1


def kelly_einsatz(p, q, cfg):
    k = (p * q - 1) / (q - 1)
    anteil = max(0.0, min(k * cfg["kelly"], cfg["max_einsatz"]))
    return round(anteil * cfg["bankroll"] * 2) / 2, anteil


def parse_zeit(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def zeit_de(dt):
    lokal = dt.astimezone()
    return f"{WOCHENTAG[lokal.weekday()]} {lokal:%d.%m. %H:%M}"


def icon(sport_key):
    return SPORT_ICON.get(sport_key.split("_")[0], "🏟")


def name_de(n):
    return NAMEN.get(n, n)


# ───────────────────────── Analyse ─────────────────────────
def gruppen(event, markt, cfg, jetzt):
    """Liefert {gruppen_id: {buch: {auswahl: quote}}} für einen Markt.
    h2h → eine Gruppe; totals → eine Gruppe je Linie (z. B. 2.5)."""
    g = {}
    for b in event.get("bookmakers", []):
        try:
            if cfg["max_alter"] > 0 and (jetzt - parse_zeit(b["last_update"])).total_seconds() > cfg["max_alter"] * 60:
                continue
        except Exception:
            pass
        for m in b.get("markets", []):
            if m["key"] != markt:
                continue
            for o in m["outcomes"]:
                if markt == "totals":
                    gid, sel = f"{o.get('point')}", f"{name_de(o['name'])} {o.get('point')}"
                else:
                    gid, sel = "", name_de(o["name"])
                g.setdefault(gid, {}).setdefault(b["key"], {"_titel": b["title"]})[sel] = o["price"]
    return g


def analysiere(events, sport_key, sport_titel, cfg, jetzt):
    value, surebets, tafel = [], [], []
    for ev in events:
        start = parse_zeit(ev["commence_time"])
        if start < jetzt and not cfg["live"]:
            continue
        if start > jetzt + timedelta(hours=cfg["vorlauf"]):
            continue
        spiel = f"{ev['home_team']} – {ev['away_team']}"
        basis = dict(event_id=ev["id"], sport=sport_key, liga=sport_titel, spiel=spiel,
                     start=start, live=start < jetzt)
        for markt in cfg["maerkte"]:
            for gid, buecher in gruppen(ev, markt, cfg, jetzt).items():
                auswahlen = []
                for b in buecher.values():
                    auswahlen += [s for s in b if s != "_titel" and s not in auswahlen]
                if markt == "h2h" and "Unentschieden" in auswahlen and len(auswahlen) == 3:
                    auswahlen.remove("Unentschieden"); auswahlen.insert(1, "Unentschieden")
                if markt == "h2h" and len(auswahlen) not in (2, 3):
                    continue
                if markt == "totals" and len(auswahlen) != 2:
                    continue
                komplett = {k: b for k, b in buecher.items() if all(s in b for s in auswahlen)}
                if not komplett:
                    continue
                # ---- faire Wahrscheinlichkeiten ----
                fair, quelle = None, None
                if cfg["sharp"] in komplett:
                    fair, _ = ohne_marge([komplett[cfg["sharp"]][s] for s in auswahlen])
                    quelle = komplett[cfg["sharp"]]["_titel"]
                else:
                    andere = [b for k, b in komplett.items()]
                    if len(andere) >= cfg["min_buecher"]:
                        summen = [0.0] * len(auswahlen)
                        for b in andere:
                            p, _ = ohne_marge([b[s] for s in auswahlen])
                            summen = [a + x for a, x in zip(summen, p)]
                        fair = [x / len(andere) for x in summen]
                        quelle = f"Konsens ({len(andere)} Buchm.)"
                # ---- beste Quoten / Surebets ----
                beste = {}
                pool = [k for k in komplett if k not in BOERSEN] or list(komplett)
                for s in auswahlen:
                    k = max(pool, key=lambda k: komplett[k][s])
                    beste[s] = (komplett[k][s], komplett[k]["_titel"])
                summe = sum(1 / beste[s][0] for s in auswahlen)
                if summe < 1:
                    surebets.append(dict(basis, markt=markt, linie=gid, gewinn=1 / summe - 1,
                                         beine=[(s, *beste[s], (1 / beste[s][0]) / summe) for s in auswahlen]))
                if markt == "h2h":
                    tafel.append(dict(basis, auswahlen=[
                        (s, beste[s][0], beste[s][1], (1 / fair[i]) if fair else None)
                        for i, s in enumerate(auswahlen)], quelle=quelle,
                        buecher=len(komplett)))
                if not fair:
                    continue
                # ---- Value ----
                ziele = cfg["ziel"] or [k for k in komplett if k != cfg["sharp"]]
                for zk in ziele:
                    if zk not in komplett or zk == cfg["sharp"]:
                        continue
                    for i, s in enumerate(auswahlen):
                        q = komplett[zk][s]
                        q_eff = q * (1 - cfg["steuer"])
                        p = fair[i]
                        edge = p * q_eff - 1
                        if not (cfg["min_q"] <= q <= cfg["max_q"]):
                            continue
                        if cfg["min_edge"] <= edge <= cfg["max_edge"]:
                            einsatz, anteil = kelly_einsatz(p, q_eff, cfg)
                            value.append(dict(basis, markt=markt, auswahl=s, quote=q, buch=komplett[zk]["_titel"],
                                              buch_key=zk, p=p, fair_q=1 / p, edge=edge, quelle=quelle,
                                              einsatz=einsatz, anteil=anteil))
    return value, surebets, tafel


# ───────────────────────── Ausgabe: Telegram ─────────────────────────
def telegram(cfg, text):
    if not (cfg["TELEGRAM_TOKEN"] and cfg["TELEGRAM_CHAT_ID"]):
        return False
    url = f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/sendMessage"
    daten = urllib.parse.urlencode({"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": text,
                                    "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=daten), timeout=20) as r:
            return r.status == 200
    except Exception as e:
        print(f"⚠  Telegram-Fehler: {e}")
        return False


def tg_value(v, cfg):
    e = html.escape
    live = " 🔴 LIVE" if v["live"] else ""
    return (f"🟢 <b>VALUE BET  +{v['edge'] * 100:.1f} %</b>{live}\n"
            f"{icon(v['sport'])} {e(v['liga'])} · {zeit_de(v['start'])}\n"
            f"<b>{e(v['spiel'])}</b>\n"
            f"Tipp: <b>{e(v['auswahl'])}</b> @ <b>{v['quote']:.2f}</b> ({e(v['buch'])})\n"
            f"Fair: {v['fair_q']:.2f} · p = {v['p'] * 100:.1f} % ({e(v['quelle'])})\n"
            f"Einsatz: <b>{v['einsatz']:.2f} €</b> ({v['anteil'] * 100:.1f} % Bankroll)")


def tg_sure(s):
    e = html.escape
    beine = "\n".join(f"• {e(a)} @ {q:.2f} ({e(b)}) → {anteil * 100:.1f} %" for a, q, b, anteil in s["beine"])
    return (f"💎 <b>SUREBET  +{s['gewinn'] * 100:.2f} %</b>\n"
            f"{icon(s['sport'])} {e(s['liga'])} · {zeit_de(s['start'])}\n<b>{e(s['spiel'])}</b>\n{beine}\n"
            f"<i>Einsatz-Verteilung in % des Gesamteinsatzes. Quoten vor Abgabe prüfen!</i>")


# ───────────────────────── Ausgabe: HTML-Bericht ─────────────────────────
CSS = """
:root{--bg:#0d1117;--card:#161b22;--c2:#1c232d;--line:#2a3340;--t:#e6edf3;--m:#8b98a8;--g:#3fd47a;--r:#ff5c6c;--a:#ffb84d;--v:#b48cff;--b:#4ea1ff;--c:#3cd0d6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;padding:16px;max-width:1100px;margin:auto}
h1{font-size:22px;margin:0}.sub{color:var(--m);font-size:13px;margin:2px 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--k);border-radius:12px;padding:14px;margin-bottom:14px}
.card h2{margin:0 0 10px;font-size:16px;color:var(--k)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}.chip{font:12px ui-monospace,Consolas,monospace;background:#0f1520;border:1px solid var(--line);color:var(--c);padding:3px 8px;border-radius:99px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.kpi{background:var(--c2);border-radius:10px;padding:10px}.kpi .k{font-size:11px;color:var(--m);text-transform:uppercase}.kpi .v{font-size:20px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:11px;color:var(--m);text-transform:uppercase}
.badge{display:inline-block;padding:2px 9px;border-radius:99px;font-weight:700;font-size:12px;white-space:nowrap}
.bg{background:rgba(63,212,122,.15);color:var(--g);border:1px solid var(--g)}.ba{background:rgba(255,184,77,.15);color:var(--a);border:1px solid var(--a)}
.bv{background:rgba(180,140,255,.15);color:var(--v);border:1px solid var(--v)}.br{background:rgba(255,92,108,.15);color:var(--r);border:1px solid var(--r)}
.m{color:var(--m);font-size:12px}.g{color:var(--g)}.best{color:var(--g);font-weight:700}.wrap{overflow-x:auto}
.leer{color:var(--m);padding:8px 0}
"""


def bericht(value, surebets, tafel, info, cfg):
    e = html.escape
    jetzt = datetime.now().astimezone()
    rows_v = "".join(
        f"<tr><td><span class='badge {'bg' if v['edge'] >= 0.05 else 'ba'}'>+{v['edge'] * 100:.1f} %</span></td>"
        f"<td>{icon(v['sport'])} <b>{e(v['spiel'])}</b>{' <span class=\"badge br\">LIVE</span>' if v['live'] else ''}"
        f"<div class='m'>{e(v['liga'])} · {zeit_de(v['start'])}</div></td>"
        f"<td><b>{e(v['auswahl'])}</b></td><td class='best'>{v['quote']:.2f}<div class='m'>{e(v['buch'])}</div></td>"
        f"<td>{v['fair_q']:.2f}<div class='m'>{v['p'] * 100:.1f} % · {e(v['quelle'])}</div></td>"
        f"<td><b>{v['einsatz']:.2f} €</b><div class='m'>{v['anteil'] * 100:.1f} %</div></td></tr>"
        for v in sorted(value, key=lambda x: -x["edge"]))
    rows_s = "".join(
        f"<tr><td><span class='badge bv'>+{s['gewinn'] * 100:.2f} %</span></td><td>{icon(s['sport'])} <b>{e(s['spiel'])}</b>"
        f"<div class='m'>{e(s['liga'])} · {zeit_de(s['start'])}</div></td><td>"
        + "<br>".join(f"{e(a)} @ <b>{q:.2f}</b> <span class='m'>{e(b)} · {an * 100:.1f} %</span>" for a, q, b, an in s["beine"])
        + "</td></tr>" for s in sorted(surebets, key=lambda x: -x["gewinn"]))

    def zelle(a):
        s, q, b, fq = a
        mark = "best" if fq and q > fq else ""
        return (f"<td><div class='m'>{e(s)}</div><span class='{mark}'>{q:.2f}</span> <span class='m'>{e(b)}</span>"
                f"<div class='m'>fair {fq:.2f}</div></td>" if fq else
                f"<td><div class='m'>{e(s)}</div>{q:.2f} <span class='m'>{e(b)}</span></td>")
    rows_t = "".join(
        f"<tr><td>{icon(t['sport'])} <b>{e(t['spiel'])}</b><div class='m'>{e(t['liga'])} · {zeit_de(t['start'])} · {t['buecher']} Buchm.</div></td>"
        + "".join(zelle(a) for a in t["auswahlen"]) + "</tr>"
        for t in sorted(tafel, key=lambda x: x["start"]))
    rest = info.get("rest", "?")
    html_doc = f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>WettBot – Bericht</title><style>{CSS}</style></head><body>
<h1>⚡ WettBot – Live-Quoten</h1><div class="sub">Stand {jetzt:%d.%m.%Y %H:%M:%S} · {e(info.get('modus', ''))} · Seite lädt alle 5 Min. neu</div>
<div class="card" style="--k:var(--b)"><h2>Übersicht</h2><div class="kpis">
<div class="kpi"><div class="k">Value Bets</div><div class="v g">{len(value)}</div></div>
<div class="kpi"><div class="k">Surebets</div><div class="v" style="color:var(--v)">{len(surebets)}</div></div>
<div class="kpi"><div class="k">Spiele gescannt</div><div class="v">{len(tafel)}</div></div>
<div class="kpi"><div class="k">API-Credits übrig</div><div class="v">{e(str(rest))}</div></div>
<div class="kpi"><div class="k">Nächster Scan</div><div class="v">{e(info.get('naechster', '–'))}</div></div></div></div>
<div class="card" style="--k:var(--g)"><h2>🟢 Value Bets</h2>
<div class="chips"><span class="chip">p_fair = (1/Q_sharp) / Σ(1/Q)</span><span class="chip">Value = p_fair · Q − 1</span><span class="chip">Einsatz = Kelly × {cfg['kelly']:g}</span></div>
<div class="wrap">{"<table><tr><th>Value</th><th>Spiel</th><th>Tipp</th><th>Quote</th><th>Fair</th><th>Einsatz</th></tr>" + rows_v + "</table>" if rows_v else "<div class='leer'>Gerade keine Value Bets über " + f"{cfg['min_edge'] * 100:g}" + " %. Normal – echte Value Bets sind selten.</div>"}</div></div>
<div class="card" style="--k:var(--v)"><h2>💎 Surebets</h2><div class="chips"><span class="chip">Σ(1/Q_beste) &lt; 1</span><span class="chip">Gewinn = 1/Σ − 1</span></div>
<div class="wrap">{"<table><tr><th>Gewinn</th><th>Spiel</th><th>Beine</th></tr>" + rows_s + "</table>" if rows_s else "<div class='leer'>Keine Surebets gefunden.</div>"}</div></div>
<div class="card" style="--k:var(--c)"><h2>📋 Quoten-Vergleich (1X2 / Sieger)</h2><div class="chips"><span class="chip">grün = beste Quote liegt über fairer Quote</span></div>
<div class="wrap">{"<table><tr><th>Spiel</th><th colspan='3'>Beste Quote je Ausgang</th></tr>" + rows_t + "</table>" if rows_t else "<div class='leer'>Keine Spiele im Zeitfenster.</div>"}</div></div>
<div class="m">Keine Garantie. Quoten vor Abgabe beim Buchmacher prüfen. Nur Buchmacher mit deutscher Lizenz (GGL-Whitelist) nutzen. Hilfe: check-dein-spiel.de · 0800 1 37 27 00</div>
</body></html>"""
    with open(BERICHT, "w", encoding="utf-8") as f:
        f.write(html_doc)


# ───────────────────────── Zustand / Log ─────────────────────────
def lade_state():
    try:
        with open(STATE_DATEI, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"gemeldet": {}}


def speichere_state(st):
    grenze = time.time() - 4 * 86400
    st["gemeldet"] = {k: t for k, t in st["gemeldet"].items() if t > grenze}
    with open(STATE_DATEI, "w", encoding="utf-8") as f:
        json.dump(st, f)


def logge_csv(v):
    neu = not os.path.exists(CSV_DATEI)
    with open(CSV_DATEI, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if neu:
            w.writerow(["Zeit", "Liga", "Spiel", "Anstoss", "Markt", "Tipp", "Buchmacher", "Quote",
                        "Faire_Quote", "Value_%", "Einsatz_EUR", "Quelle", "Ergebnis"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M"), v["liga"], v["spiel"],
                    v["start"].astimezone().strftime("%Y-%m-%d %H:%M"), v["markt"], v["auswahl"], v["buch"],
                    f"{v['quote']:.2f}".replace(".", ","), f"{v['fair_q']:.2f}".replace(".", ","),
                    f"{v['edge'] * 100:.1f}".replace(".", ","), f"{v['einsatz']:.2f}".replace(".", ","),
                    v["quelle"], ""])


# ───────────────────────── Demo-Daten ─────────────────────────
def demo_events(jetzt):
    teams = [("Bayern München", "Borussia Dortmund"), ("RB Leipzig", "Bayer Leverkusen"),
             ("VfB Stuttgart", "Eintracht Frankfurt"), ("SC Freiburg", "Werder Bremen"),
             ("1. FC Köln", "Hamburger SV"), ("Union Berlin", "VfL Wolfsburg")]
    buecher = [("pinnacle", "Pinnacle", 0.025, 0.005), ("tipico_de", "Tipico (DE)", 0.07, 0.05),
               ("winamax_de", "Winamax (DE)", 0.06, 0.05), ("betsson", "Betsson", 0.06, 0.03),
               ("williamhill", "William Hill", 0.065, 0.03), ("sport888", "888sport", 0.07, 0.03),
               ("unibet_nl", "Unibet (NL)", 0.06, 0.03), ("betfair_ex_eu", "Betfair Exchange", 0.01, 0.02)]
    rnd = random.Random(int(jetzt.timestamp() // 600))
    events = []
    for i, (h, a) in enumerate(teams):
        ph = rnd.uniform(0.3, 0.6); pd = rnd.uniform(0.22, 0.28); pa = 1 - ph - pd
        start = jetzt + timedelta(hours=3 + i * 5)
        bms = []
        for key, titel, marge, rausch in buecher:
            outs = []
            for n, p in ((h, ph), ("Draw", pd), (a, pa)):
                q = 1 / (p * (1 + marge)) * (1 + rnd.uniform(-rausch, rausch))
                if key == "winamax_de" and i in (1, 4) and n == a:
                    q = 1 / p * 1.07          # Demo: Buchmacher hat Quote "verschlafen"
                if key == "tipico_de" and i == 5 and n == h:
                    q = 1 / p * 1.12          # Demo: Ausreißer → Surebet
                outs.append({"name": n, "price": round(q, 2)})
            bms.append({"key": key, "title": titel, "last_update": jetzt.isoformat(),
                        "markets": [{"key": "h2h", "outcomes": outs}]})
        events.append({"id": f"demo{i}", "home_team": h, "away_team": a,
                       "commence_time": start.isoformat(), "bookmakers": bms})
    return events


# ───────────────────────── Ablauf ─────────────────────────
def intervall_berechnen(cfg, rest, kosten_pro_scan):
    if cfg["intervall"] > 0:
        return cfg["intervall"] * 60
    jetzt = datetime.now(timezone.utc)
    naechster_monat = (jetzt.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sek_rest = (naechster_monat - jetzt).total_seconds()
    try:
        verfuegbar = int(float(rest)) - cfg["reserve"]
    except Exception:
        return 3 * 3600
    if verfuegbar < kosten_pro_scan:
        return None
    scans = verfuegbar // max(1, kosten_pro_scan)
    return max(cfg["min_intervall"] * 60, sek_rest / scans)


def scan(cfg, state, demo=False, sportliste=None):
    jetzt = datetime.now(timezone.utc)
    alle_v, alle_s, alle_t, info = [], [], [], {}
    if demo:
        v, s, t = analysiere(demo_events(jetzt), "soccer_demo", "Bundesliga (DEMO)", cfg, jetzt)
        alle_v, alle_s, alle_t = v, s, t
        info = {"rest": "DEMO", "modus": "DEMO-Modus mit Fantasie-Quoten", "kosten": 0}
    else:
        rest, kosten = "?", 0
        for sport in sportliste:
            try:
                events, kopf = hole_quoten(cfg, sport)
            except ApiFehler as e:
                print(f"   ✗ {sport}: {e}")
                if "Credits" in str(e) or "Key" in str(e):
                    raise
                continue
            rest = kopf.get("x-requests-remaining", rest)
            kosten += int(float(kopf.get("x-requests-last", 0) or 0))
            titel = events[0].get("sport_title", sport) if events else sport
            v, s, t = analysiere(events, sport, titel, cfg, jetzt)
            print(f"   ✓ {titel}: {len(events)} Spiele · {len(v)} Value · {len(s)} Surebets")
            alle_v += v; alle_s += s; alle_t += t
        info = {"rest": rest, "modus": f"{len(sportliste)} Ligen · Region {cfg['REGION']} · Märkte {','.join(cfg['maerkte'])}",
                "kosten": kosten}

    # Meldungen (nur neue)
    neu = 0
    for v in sorted(alle_v, key=lambda x: -x["edge"]):
        key = f"V|{v['event_id']}|{v['markt']}|{v['auswahl']}|{v['buch_key']}"
        alt = state["gemeldet"].get(key + "|edge", 0)
        if key in state["gemeldet"] and v["edge"] < alt + 0.02:
            continue
        state["gemeldet"][key] = time.time(); state["gemeldet"][key + "|edge"] = v["edge"]
        neu += 1
        print(f"\n🟢 VALUE +{v['edge'] * 100:.1f} %  {v['spiel']}  →  {v['auswahl']} @ {v['quote']:.2f} "
              f"({v['buch']}) · fair {v['fair_q']:.2f} · Einsatz {v['einsatz']:.2f} €")
        if not demo:
            logge_csv(v)
        telegram(cfg, tg_value(v, cfg))
    for s in alle_s:
        key = f"S|{s['event_id']}|{s['markt']}|{s['linie']}"
        if key in state["gemeldet"]:
            continue
        state["gemeldet"][key] = time.time(); neu += 1
        print(f"\n💎 SUREBET +{s['gewinn'] * 100:.2f} %  {s['spiel']}")
        telegram(cfg, tg_sure(s))
    return alle_v, alle_s, alle_t, info, neu


def main():
    ap = argparse.ArgumentParser(description="WettBot – Live-Quoten-Scanner")
    ap.add_argument("--einmal", action="store_true", help="nur ein Scan")
    ap.add_argument("--demo", action="store_true", help="Fantasie-Daten, kein API-Key nötig")
    ap.add_argument("--sportarten", action="store_true", help="aktive Sportarten anzeigen (0 Credits)")
    ap.add_argument("--telegram-test", action="store_true", help="Testnachricht senden")
    ap.add_argument("--config", default=os.path.join(BASIS, "config.env"))
    a = ap.parse_args()
    cfg = lade_config(a.config)

    print("⚡ WettBot – Live-Quoten-Scanner\n")
    if a.telegram_test:
        ok = telegram(cfg, "✅ <b>WettBot</b> ist verbunden!")
        print("Telegram: gesendet ✓" if ok else "Telegram: fehlgeschlagen – TOKEN/CHAT_ID prüfen.")
        return
    if not a.demo and not cfg["ODDS_API_KEY"]:
        print("✗ Kein ODDS_API_KEY in config.env.\n  Kostenlos holen: https://the-odds-api.com\n"
              "  Oder erst testen:  python wettbot.py --demo")
        return
    if a.sportarten:
        for s in aktive_sportarten(cfg["ODDS_API_KEY"]):
            if s.get("active"):
                print(f"  {icon(s['key'])} {s['key']:<42} {s['title']}")
        return

    state = lade_state()
    sportliste = []
    if not a.demo:
        try:
            sportliste = loese_sportarten(cfg)
        except ApiFehler as e:
            print(f"✗ {e}"); return
        if not sportliste:
            print("✗ Keine der eingestellten Sportarten hat gerade Spiele. Prüfe SPORTARTEN (--sportarten)."); return
        kosten = len(sportliste) * len(cfg["maerkte"]) * len(cfg["REGION"].split(","))
        print(f"Ligen: {', '.join(sportliste)}\nKosten pro Scan: {kosten} Credits\n")
    else:
        kosten = 0

    erster = True
    while True:
        print(f"── Scan {datetime.now():%d.%m. %H:%M:%S} ──")
        try:
            v, s, t, info, neu = scan(cfg, state, demo=a.demo, sportliste=sportliste)
        except ApiFehler as e:
            print(f"✗ {e}"); break
        if not a.demo:
            speichere_state(state)
        warte = None if (a.einmal or a.demo) else intervall_berechnen(cfg, info.get("rest"), kosten)
        info["naechster"] = (datetime.now() + timedelta(seconds=warte)).strftime("%H:%M") if warte else "–"
        bericht(v, s, t, info, cfg)
        print(f"\n   {len(v)} Value · {len(s)} Surebets · {neu} neu gemeldet · Credits übrig: {info.get('rest')}")
        print(f"   Bericht: {BERICHT}")
        if erster and cfg["oeffnen"]:
            try:
                webbrowser.open("file://" + BERICHT)
            except Exception:
                pass
            erster = False
        if a.einmal or a.demo:
            break
        if warte is None:
            print("⏸  Credits für diesen Monat (fast) aufgebraucht – Bot stoppt. Reset am 1.")
            break
        print(f"   Nächster Scan um {info['naechster']} (Credits werden bis Monatsende eingeteilt)\n")
        try:
            time.sleep(warte)
        except KeyboardInterrupt:
            print("\nBeendet."); break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBeendet.")

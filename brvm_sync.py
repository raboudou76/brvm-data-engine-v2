"""BRVM public dividend listing sync. No invented data; fail closed.
Only public HTML table is parsed; site layout changes produce a visible warning.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup

URL = "https://www.brvm.org/fr/esv/paiement-de-dividendes"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BRVM-RADAR/1.8; financial research)"}

def _amount(value):
    s = str(value).replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"(?:\d+\.?\d*|\.\d+)", s)
    return float(m.group(0)) if m else None

def parse_dividend_page(html, page_url=URL):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for table in soup.select("table"):
        headings = [h.get_text(" ", strip=True).casefold() for h in table.select("thead th")]
        if not headings:
            first = table.find("tr")
            headings = [c.get_text(" ", strip=True).casefold() for c in first.find_all(["th", "td"])] if first else []
        def col(*tokens):
            return next((i for i, h in enumerate(headings) if all(t in h for t in tokens)), None)
        issuer_i, year_i, net_i = col("emetteur"), col("exercice"), col("dividende", "net")
        if issuer_i is None: issuer_i = col("émetteur")
        if None in (issuer_i, year_i, net_i): continue
        pay_i, ex_i, notice_i = col("date", "paiement"), col("ex-dividende"), col("avis")
        for tr in table.select("tbody tr") or table.select("tr")[1:]:
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) <= max(issuer_i, year_i, net_i): continue
            get = lambda i: cells[i].get_text(" ", strip=True) if i is not None and i < len(cells) else ""
            issuer, year_text, raw = get(issuer_i), get(year_i), get(net_i)
            ym = re.search(r"20\d{2}", year_text)
            amount = _amount(raw)
            if not issuer or not ym or amount is None or amount < 0: continue
            notice = cells[notice_i].find("a", href=True) if notice_i is not None and notice_i < len(cells) else None
            found.append({"Société": issuer, "Année": int(ym.group()), "Dividende net BRVM": amount,
                "Unité publiée": raw, "Date de paiement": get(pay_i), "Date ex-dividende": get(ex_i),
                "Source": urljoin(page_url, notice["href"]) if notice else page_url})
    return found

def fetch_dividends(max_pages=12, timeout=12):
    """Read up to max_pages of public BRVM event pages; no guarantee of exhaustive coverage."""
    max_pages = min(max(int(max_pages), 1), 30)
    rows = []
    with requests.Session() as session:
        session.headers.update(HEADERS)
        for page in range(max_pages):
            url = URL if page == 0 else f"{URL}?page={page}"
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            current = parse_dividend_page(response.text, url)
            if not current:
                if page == 0: raise ValueError("Tableau des dividendes BRVM non reconnu ou indisponible.")
                break
            rows.extend(current)
    data = pd.DataFrame(rows)
    if data.empty: raise ValueError("Aucune annonce de dividende exploitable.")
    data = data.drop_duplicates(subset=["Société", "Année", "Dividende net BRVM", "Date de paiement"])
    return data, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def for_issuer(data, issuer, smb_personal_override=True):
    """Net values BRVM are not necessarily the personal investor's after-tax net.
    SMB 2025 is a user-confirmed 704 FCFA personal net; show both side by side.
    """
    selected = data.loc[data["Société"].eq(issuer)].sort_values(["Année", "Date de paiement"])
    selected = selected.drop_duplicates(subset=["Année"], keep="last").tail(5).copy()
    selected["Dividende brut"] = float("nan")
    selected["Dividende net"] = selected["Dividende net BRVM"]
    selected["Statut"] = "Montant net annoncé BRVM ; profil fiscal à confirmer"
    if smb_personal_override and issuer.strip().upper() in ("SMB", "SMB CI", "SMB COTE D'IVOIRE"):
        mask = selected["Année"].eq(2025)
        if mask.any():
            selected.loc[mask, "Dividende net"] = 704.0
            selected.loc[mask, "Statut"] = "704 FCFA net personne physique : valeur confirmée par utilisateur ; BRVM affiche 800 FCFA"
    return selected

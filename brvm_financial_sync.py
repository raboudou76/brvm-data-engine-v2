"""BRVM official issuer reports discovery V2.2. Financial parsing unchanged."""
from datetime import datetime, timezone
import re
from urllib.parse import urljoin
import pandas as pd
from bs4 import BeautifulSoup
from auto_financials import BASE, FIN, get_html, safe_url, download_pdf, discover, norm
from structured_financials import extract_structured
from brvm_registry import identities

INDEX = BASE + '/fr/rapports-societe-cotes'
METRICS = {'chiffre_affaires': "Chiffre d'affaires", 'pnb': "Chiffre d'affaires", 'resultat_net': 'Résultat net'}


def _annual(title):
    t = norm(title)
    # Certified financial statements are valid even if the title mentions auditors.
    if any(x in t for x in ('semestre', 'trimestre', 'trimestriel', 'semestriel', 'attestation', 'rapport special', 'conventions reglementees')):
        return False
    return any(x in t for x in ('etats financiers', 'etat financier', 'rapport annuel', 'comptes annuels', 'financial statements', 'exercice'))


def _society_pages(issuer):
    spec = identities(issuer.strip().upper())
    guesses = list(spec['slugs'])
    if issuer.upper() == 'SMBC': guesses.insert(0, 'smb')
    if not guesses: guesses.append(issuer.lower())
    try:
        soup = get_html(INDEX)
        aliases = [norm(x) for x in spec['aliases'] if len(x) > 3]
        for a in soup.select('a[href]'):
            href = urljoin(BASE, a['href'])
            if '/rapports-societe-cotes/' in href and any(alias in norm(a.get_text(' ', strip=True)) for alias in aliases):
                guesses.append(href.rsplit('/', 1)[-1].split('?')[0])
    except Exception:
        pass
    return list(dict.fromkeys(INDEX + '/' + re.sub(r'[^a-z0-9-]', '-', s.strip('/').lower()) for s in guesses if s))


def _extract_links(soup, page_url):
    """Issuer listing may link to publication detail pages, not PDF files."""
    direct = []
    details = []
    for a in soup.select('a[href]'):
        href = urljoin(page_url, a['href'])
        if not safe_url(href): continue
        is_pdf = bool(re.search(r'\.pdf(?:$|\?)', href, re.I))
        container = a.find_parent('tr') or a.find_parent('article') or a.parent
        context = container.get_text(' ', strip=True) if container else a.get_text(' ', strip=True)
        title = re.sub(r'\s+', ' ', context.replace('Télécharger', '')).strip()
        if not _annual(title): continue
        item = {'Titre': title[:240], 'Contexte': title[:300], 'URL': href, 'PDF': is_pdf, 'Page source': page_url}
        if is_pdf: direct.append(item)
        elif href != page_url and '/rapports-societe-cotes/' not in href and not href.endswith('#'):
            details.append(item)
    return direct, details


def find_reports(issuer, pages=5):
    if not issuer or len(issuer.strip()) < 2: return []
    results = {}
    for issuer_page in _society_pages(issuer):
        for page in range(max(1, min(int(pages), 8))):
            url = issuer_page + (f'?page={page}' if page else '')
            try: soup = get_html(url)
            except Exception: break
            pdfs, detail_links = _extract_links(soup, url)
            for item in pdfs: results[item['URL']] = item
            # Follow publication details, including certified financial statements.
            for detail in detail_links[:35]:
                try: sub = get_html(detail['URL'])
                except Exception: continue
                for a in sub.select('a[href]'):
                    pdf_url = urljoin(detail['URL'], a['href'])
                    if safe_url(pdf_url) and re.search(r'\.pdf(?:$|\?)', pdf_url, re.I):
                        results[pdf_url] = {'Titre': detail['Titre'], 'Contexte': detail['Contexte'], 'URL': pdf_url, 'PDF': True, 'Page source': detail['URL']}
            if page and not soup.select('a[href*="page="]'): break
        if results: break
    if not results:
        spec = identities(issuer)
        for query in [issuer] + spec['aliases']:
            try:
                for item in discover(query, pages=pages, max_detail=24):
                    if item.get('PDF') and _annual(item.get('Titre', '') + ' ' + item.get('Contexte', '')):
                        results[item['URL']] = item
            except Exception: continue
            if results: break
    def sortkey(r):
        years = re.findall(r'20\d{2}', r['Titre'])
        return (int(years[-1]) if years else 0, r['Titre'])
    return sorted(results.values(), key=sortkey, reverse=True)


def parse_report(pdf_bytes, url, title):
    _, metrics, _ = extract_structured(pdf_bytes)
    if metrics.empty: return []
    rows = []
    for _, r in metrics.iterrows():
        key = r['Indicateur']
        if key not in METRICS or r['Unité'] != 'millions FCFA': continue
        for year_col, _ in [('Exercice N', 'N'), ('Exercice N-1', 'N-1')]:
            value_col = '2025 ou N' if year_col == 'Exercice N' else '2024 ou N-1'
            year = pd.to_numeric(r[year_col], errors='coerce')
            value = pd.to_numeric(r[value_col], errors='coerce')
            if pd.notna(year) and 2000 <= year <= 2100 and pd.notna(value):
                rows.append({'Année': int(year), 'Indicateur': METRICS[key], 'Montant': float(value), 'Source': url, 'Publication': title, 'Page PDF': r['Page'], 'Statut': 'Extraction automatique à vérifier', 'Libellé source': r['Libellé source']})
    return rows


def consolidate(records):
    cols = ['Année', "Chiffre d'affaires", 'Résultat net']
    if not records: return pd.DataFrame(columns=cols), pd.DataFrame()
    detail = pd.DataFrame(records).drop_duplicates(subset=['Année', 'Indicateur', 'Montant', 'Source'])
    safe = []
    for (year, metric), group in detail.groupby(['Année', 'Indicateur']):
        vals = group['Montant'].round(4).unique()
        if len(vals) == 1: safe.append({'Année': year, 'Indicateur': metric, 'Montant': vals[0]})
        else: detail.loc[group.index, 'Statut'] = 'Conflit de montants — non affiché'
    if not safe: return pd.DataFrame(columns=cols), detail
    history = pd.DataFrame(safe).pivot(index='Année', columns='Indicateur', values='Montant').reset_index()
    history.columns.name = None
    for col in cols:
        if col not in history: history[col] = float('nan')
    return history[cols].sort_values('Année').tail(5), detail


def sync_financials(reports, max_reports=5):
    records, errors = [], []
    for report in reports[:max_reports]:
        try:
            raw = download_pdf(report['URL'])
            found = parse_report(raw, report['URL'], report['Titre'])
            if not found: errors.append(f"{report['Titre'][:70]} : PDF lu, aucune ligne financière extraite")
            records.extend(found)
        except Exception as exc: errors.append(f"{report['Titre'][:70]} : {type(exc).__name__} — {str(exc)[:120]}")
    history, detail = consolidate(records)
    return history, detail, errors, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

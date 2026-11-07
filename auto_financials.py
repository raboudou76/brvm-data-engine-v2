"""Conservative discovery and candidate extraction from public BRVM financial publications.
No guessed values, no OCR, no automatic certification.
"""
import re
import unicodedata
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import pandas as pd
from finance_engine import parse_amount

BASE = 'https://www.brvm.org'
FIN = BASE + '/fr/type-document/etats-financiers'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; BRVMRadarResearch/1.3)'}
MAX_PDF = 25 * 1024 * 1024

def norm(s):
    s = unicodedata.normalize('NFKD', str(s).lower())
    return ' '.join(''.join(c for c in s if not unicodedata.combining(c)).split())

def safe_url(url):
    p = urlparse(url)
    return p.scheme == 'https' and p.hostname in {'www.brvm.org', 'brvm.org'}

def get_html(url, timeout=18):
    if not safe_url(url):
        raise ValueError('Adresse hors du domaine officiel BRVM.')
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    if not safe_url(r.url):
        raise ValueError('Redirection vers un domaine non autorisé.')
    if 'html' not in r.headers.get('content-type', '').lower():
        raise ValueError('La réponse n’est pas une page HTML.')
    if len(r.content) > 4 * 1024 * 1024:
        raise ValueError('Page trop volumineuse.')
    return BeautifulSoup(r.content, 'html.parser')

def discover(query, pages=4, max_detail=12):
    """Find relevant BRVM financial publication pages/PDFs; ranked, not certified."""
    q = norm(query)
    if len(q) < 2: return []
    entries = {}
    for page in range(max(1, min(int(pages), 8))):
        soup = get_html(f'{FIN}?page={page}')
        for a in soup.select('a[href]'):
            url = urljoin(BASE, a['href'])
            if not safe_url(url): continue
            title = a.get_text(' ', strip=True)
            context = a.parent.get_text(' ', strip=True)[:500] if a.parent else title
            blob = norm(title + ' ' + context)
            if q not in blob: continue
            if not (re.search(r'\.pdf(?:$|\?)', url, re.I) or any(x in blob for x in ['financier','rapport','resultat','activite','bilan','trimestr','semestr','annuel'])):
                continue
            if url.rstrip('/') == FIN.rstrip('/'): continue
            entries[url] = {'Titre': title or context[:130], 'Contexte': context[:300], 'URL': url, 'PDF': bool(re.search(r'\.pdf(?:$|\?)', url, re.I)), 'Page source': f'{FIN}?page={page}'}
    # Follow a limited number of candidate detail pages to find their PDF links.
    initial = list(entries.values())
    for item in initial[:max_detail]:
        if item['PDF']: continue
        try: soup = get_html(item['URL'])
        except (requests.RequestException, ValueError): continue
        for a in soup.select('a[href]'):
            url = urljoin(item['URL'], a['href'])
            if not safe_url(url) or not re.search(r'\.pdf(?:$|\?)', url, re.I): continue
            entries[url] = {'Titre': item['Titre'], 'Contexte': item['Contexte'], 'URL': url, 'PDF': True, 'Page source': item['URL']}
    return sorted(entries.values(), key=lambda e: (not e['PDF'], e['Titre']))[:80]

def download_pdf(url):
    if not safe_url(url): raise ValueError('URL non autorisée.')
    with requests.get(url, headers=HEADERS, timeout=35, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        if not safe_url(r.url): raise ValueError('Redirection PDF non autorisée.')
        chunks=[]; total=0
        for chunk in r.iter_content(65536):
            total += len(chunk)
            if total > MAX_PDF: raise ValueError('PDF supérieur à 25 Mo.')
            chunks.append(chunk)
    raw=b''.join(chunks)
    if not raw.startswith(b'%PDF-'): raise ValueError('Le lien ne retourne pas un PDF natif.')
    return raw

# Require an unambiguous row label; return candidates only, not audited metrics.
LABELS = {
    'chiffre_affaires': [r'^chiffre d.affaires(?: net)?$', r'^ventes de marchandises$'],
    'resultat_net': [r'^resultat net(?: de l.exercice)?$', r'^benefice net$'],
    'capitaux_propres': [r'^total capitaux propres$', r'^capitaux propres$'],
    'dette_financiere': [r'^emprunts et dettes financieres$', r'^dettes financieres$'],
    'flux_operationnel': [r'^flux (?:net )?de tresorerie (?:provenant|lies?) (?:des |aux )?activites operationnelles$'],
}

def candidates_from_tables(tables):
    rows=[]
    for t in tables:
        frame=t['data']
        for _, line in frame.iterrows():
            cells=[str(x).strip() for x in line.tolist()]
            if len(cells)<2: continue
            label=norm(cells[0]); metric=None
            for key, patterns in LABELS.items():
                if any(re.fullmatch(p, label) for p in patterns): metric=key; break
            if not metric: continue
            numeric=[(j, parse_amount(c)) for j,c in enumerate(cells[1:],1)]
            numeric=[(j,v) for j,v in numeric if v is not None]
            if not numeric: continue
            # Column meaning is unknown until the user verifies headers and periods.
            for j,v in numeric[:4]:
                rows.append({'Indicateur':metric, 'Libellé PDF':cells[0], 'Montant brut':v, 'Colonne PDF':j+1, 'Page PDF':t['page'], 'Méthode':t['method'], 'Statut':'À vérifier : période, unité et signe'})
    return pd.DataFrame(rows, columns=['Indicateur','Libellé PDF','Montant brut','Colonne PDF','Page PDF','Méthode','Statut'])

"""BRVM issuer publication catalogue, independent of financial parsing.
Only official BRVM pages; no third-party scraping or fabricated data.
"""
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from auto_financials import BASE, get_html, safe_url
from brvm_registry import identities, norm

ROOT = BASE + '/fr/rapports-societe-cotes/'
DIRECT = {
    'SMBC': 'smb', 'SGBC': 'sgci', 'BOAB': 'bank-africa-bn',
    'CBIBF': 'coris-bank-international', 'BOABF': 'bank-africa-bf',
    'BOAC': 'bank-africa-ci', 'BOAM': 'bank-africa-ml',
    'BOAN': 'bank-africa-ng', 'BOAS': 'bank-africa-sn',
}

def classify(title):
    t=norm(title)
    if '3e trimestre' in t or '3eme trimestre' in t or 'troisieme trimestre' in t or re.search(r'\bt3\b',t): return 'T3'
    if '1er trimestre' in t or 'premier trimestre' in t or re.search(r'\bt1\b',t): return 'T1'
    if 'semestre' in t or 'semestriel' in t or re.search(r'\bs1\b',t): return 'S1'
    if any(x in t for x in ('etats financiers','etat financier','rapport annuel','annuel','exercice','comptes annuels','rapport des commissaires aux comptes')): return 'ANNUEL'
    return 'AUTRE'

def extract_year(title):
    years=re.findall(r'(?<!\d)(20\d{2})(?!\d)',title)
    return int(years[-1]) if years else None

def _publication_rows(soup,source):
    rows=[]
    # Drupal issuer tables: PDF link is generally labelled "Télécharger".
    for tr in soup.select('tr'):
        text=re.sub(r'\s+',' ',tr.get_text(' ',strip=True)).strip()
        if not text or text.lower().startswith(('type','description','titre')): continue
        period=classify(text)
        if period=='AUTRE': continue
        links=[urljoin(source,a['href']) for a in tr.select('a[href]')]
        links=[u for u in links if safe_url(u)]
        if not links: continue
        pdfs=[u for u in links if re.search(r'\.pdf(?:$|\?)',u,re.I)]
        details=[u for u in links if u not in pdfs and u.split('#')[0]!=source.split('#')[0] and '/rapports-societe-cotes/' not in u]
        for u in pdfs or details[:1]:
            rows.append({'Titre':text.replace('Télécharger','').strip(),'Année':extract_year(text),
                         'Période':period,'URL':u,'PDF':bool(u in pdfs),'Page source':source})
    # Some templates use views-row containers rather than tables.
    if not rows:
        for block in soup.select('.views-row, article'):
            text=re.sub(r'\s+',' ',block.get_text(' ',strip=True)).strip()
            period=classify(text)
            if period=='AUTRE':continue
            for a in block.select('a[href]'):
                u=urljoin(source,a['href'])
                if safe_url(u) and (re.search(r'\.pdf(?:$|\?)',u,re.I) or '/fr/' in u):
                    rows.append({'Titre':text.replace('Télécharger','').strip()[:250],'Année':extract_year(text),
                                 'Période':period,'URL':u,'PDF':bool(re.search(r'\.pdf(?:$|\?)',u,re.I)),'Page source':source})
                    break
    return rows

def catalog(symbol,pages=3,include_details=True):
    """Return (documents, diagnostic); never hides failed HTTP requests."""
    symbol=str(symbol).upper().strip()
    slugs=list(dict.fromkeys([DIRECT.get(symbol)]+identities(symbol)['slugs']))
    slugs=[s for s in slugs if s]
    diag={'symbol':symbol,'pages':[],'errors':[],'documents_found':0,'pdf_links':0}
    found={}
    for slug in slugs:
        for page in range(max(1,min(int(pages),8))):
            url=ROOT+slug+(f'?page={page}' if page else '')
            try:
                soup=get_html(url)
                items=_publication_rows(soup,url)
                diag['pages'].append({'URL':url,'Statut':'HTTP 200','Documents':len(items)})
            except Exception as exc:
                diag['pages'].append({'URL':url,'Statut':type(exc).__name__,'Documents':0})
                diag['errors'].append(f'{url} : {type(exc).__name__}: {str(exc)[:150]}')
                break
            for item in items:
                if not item['PDF'] and include_details:
                    try:
                        detail=get_html(item['URL'])
                        pdfs=[urljoin(item['URL'],a['href']) for a in detail.select('a[href]')]
                        pdfs=[u for u in pdfs if safe_url(u) and re.search(r'\.pdf(?:$|\?)',u,re.I)]
                        if pdfs:
                            for pdf in pdfs:
                                record=dict(item,URL=pdf,PDF=True,**{'Page source':item['URL']})
                                found[pdf]=record
                            continue
                    except Exception as exc:
                        diag['errors'].append(f"Détail {item['URL']} : {type(exc).__name__}: {str(exc)[:100]}")
                found[item['URL']]=item
            # First valid issuer page is authoritative; avoid unnecessary alternative slugs.
            if page==0 and not items: continue
        if found:break
    documents=sorted(found.values(),key=lambda x:(x['Année'] or 0,x['Période'],x['Titre']),reverse=True)
    diag['documents_found']=len(documents)
    diag['pdf_links']=sum(bool(x['PDF']) for x in documents)
    return documents,diag

def annual_pdf_reports(symbol,pages=3):
    documents,diag=catalog(symbol,pages)
    return [{'Titre':d['Titre'],'Contexte':symbol,'URL':d['URL'],'PDF':True,'Page source':d['Page source']} for d in documents if d['Période']=='ANNUEL' and d['PDF']],diag

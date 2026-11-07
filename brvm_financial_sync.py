"""Recherche directe des publications annuelles sur les pages sociétés BRVM."""
from datetime import datetime,timezone
import re
from urllib.parse import urljoin
import pandas as pd
from bs4 import BeautifulSoup
from auto_financials import BASE, FIN, get_html, safe_url, download_pdf, discover, norm
from structured_financials import extract_structured

INDEX=BASE+'/fr/rapports-societe-cotes'
METRICS={'chiffre_affaires':"Chiffre d'affaires",'pnb':"Chiffre d'affaires",'resultat_net':'Résultat net'}
from brvm_registry import identities

def _annual(title):
    t=norm(title)
    return not any(x in t for x in ('semestre','trimestre','trimestriel','semestriel','activites','commissaires aux comptes','attestation')) and any(x in t for x in ('financier','annuel','exercice','rapport annuel'))

def _society_pages(issuer):
    issuer=issuer.strip(); code=issuer.upper()
    identity=identities(code)
    guesses=identity['slugs']+[issuer.lower()]
    guesses.extend(identity['slugs'])
    if code=='SMBC': guesses.insert(0,'smb')
    # Index is optional: a failure must not prevent known direct issuer pages.
    try:
        soup=get_html(INDEX)
        terms=[norm(x) for x in identity['aliases']]
        for a in soup.select('a[href]'):
            href=urljoin(BASE,a['href']); label=norm(a.get_text(' ',strip=True))
            if '/rapports-societe-cotes/' in href and any(t in label or t in norm(href) for t in terms if len(t)>2):
                guesses.append(href.rsplit('/',1)[-1].split('?')[0])
    except Exception: pass
    return list(dict.fromkeys(INDEX+'/'+re.sub(r'[^a-z0-9-]','-',x.strip('/').lower()) for x in guesses if x))

def find_reports(issuer,pages=5):
    if not issuer or len(issuer.strip())<2: return []
    results={}
    for page_url in _society_pages(issuer):
        for p in range(max(1,min(int(pages),8))):
            url=page_url+(f'?page={p}' if p else '')
            try: soup=get_html(url)
            except Exception: break
            for a in soup.select('a[href]'):
                href=urljoin(url,a['href'])
                if not safe_url(href) or not re.search(r'\.pdf(?:$|\?)',href,re.I): continue
                row=a.find_parent('tr') or a.parent
                title=row.get_text(' ',strip=True).replace('Télécharger','').strip() if row else a.get_text(' ',strip=True)
                if not _annual(title): continue
                results[href]={'Titre':title,'Contexte':issuer,'URL':href,'PDF':True,'Page source':url}
            if not soup.select('a[href*="page="]') and p>0: break
        if results: break
    if not results:
        try:
            for r in discover(issuer,pages=pages,max_detail=18):
                if r.get('PDF') and _annual(r.get('Titre','')+' '+r.get('Contexte','')): results[r['URL']]=r
        except Exception: pass
    return sorted(results.values(),key=lambda r: (int(re.search(r'20\d{2}',r['Titre']).group()) if re.search(r'20\d{2}',r['Titre']) else 0,r['Titre']),reverse=True)

def parse_report(pdf_bytes,url,title):
    _,metrics,_=extract_structured(pdf_bytes)
    if metrics.empty:return []
    rows=[]
    for _,r in metrics.iterrows():
        key=r['Indicateur']
        if key not in METRICS or r['Unité']!='millions FCFA': continue
        for year_col,val_col in [('Exercice N','2025 ou N'),('Exercice N-1','2024 ou N-1')]:
            year=pd.to_numeric(r[year_col],errors='coerce'); value=pd.to_numeric(r[val_col],errors='coerce')
            if pd.notna(year) and 2000<=year<=2100 and pd.notna(value):
                rows.append({'Année':int(year),'Indicateur':METRICS[key],'Montant':float(value),'Source':url,'Publication':title,'Page PDF':r['Page'],'Statut':'Extraction automatique à vérifier','Libellé source':r['Libellé source']})
    return rows

def consolidate(records):
    cols=['Année',"Chiffre d'affaires",'Résultat net']
    if not records:return pd.DataFrame(columns=cols),pd.DataFrame()
    detail=pd.DataFrame(records).drop_duplicates(subset=['Année','Indicateur','Montant','Source'])
    safe=[]
    for (year,metric),group in detail.groupby(['Année','Indicateur']):
        vals=group['Montant'].round(4).unique()
        if len(vals)==1: safe.append({'Année':year,'Indicateur':metric,'Montant':vals[0]})
        else: detail.loc[group.index,'Statut']='Conflit de montants — non affiché'
    if not safe:return pd.DataFrame(columns=cols),detail
    history=pd.DataFrame(safe).pivot(index='Année',columns='Indicateur',values='Montant').reset_index()
    history.columns.name=None
    for col in cols:
        if col not in history: history[col]=float('nan')
    return history[cols].sort_values('Année').tail(5),detail

def sync_financials(reports,max_reports=5):
    records=[];errors=[]
    for report in reports[:max_reports]:
        try:
            raw=download_pdf(report['URL']); found=parse_report(raw,report['URL'],report['Titre'])
            if not found:errors.append(f"{report['Titre'][:70]} : PDF lu, aucune ligne financière validée (mise en page, année ou unité)")
            records.extend(found)
        except Exception as exc: errors.append(f"{report['Titre'][:70]} : {type(exc).__name__} — {str(exc)[:120]}")
    history,detail=consolidate(records)
    return history,detail,errors,datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

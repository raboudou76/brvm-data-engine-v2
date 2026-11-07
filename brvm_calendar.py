"""Official BRVM dividend calendar fallback; PDF is parsed at runtime, not a static financial dataset."""
import re
from datetime import datetime,timezone
import pandas as pd
import requests
import fitz
from brvm_registry import norm,identities
from auto_financials import safe_url

CALENDARS=[
 'https://www.brvm.org/sites/default/files/20261009_-_calendrier_de_paiement_des_dividendes_-_exercice_2025.pdf',
]

def _number(value):
    s=str(value).replace('\u202f','').replace('\xa0','').replace(' ','').replace(',','.')
    m=re.search(r'\d+(?:\.\d+)?',s)
    return float(m.group()) if m else None

def calendar_for_issuer(symbol,name='',urls=None):
    """Only exact issuer aliases in a named calendar row; 2025 is fiscal year, not payment year."""
    allowed={norm(x) for x in identities(symbol,name)['aliases']}
    rows=[];errors=[]
    for url in (urls if urls is not None else CALENDARS):
        if not safe_url(url):continue
        try:
            r=requests.get(url,timeout=25,headers={'User-Agent':'Mozilla/5.0'});r.raise_for_status()
            if not r.content.startswith(b'%PDF-'):raise ValueError('Réponse non PDF')
            doc=fitz.open(stream=r.content,filetype='pdf')
            text='\n'.join(p.get_text(sort=True) for p in doc)
            # Prefer PDF table extraction via line-level matching; fail closed if a row is ambiguous.
            lines=[x.strip() for x in text.splitlines() if x.strip()]
            for i,line in enumerate(lines):
                for alias in allowed:
                    if len(alias)<4 or norm(line)!=alias:continue
                    nearby=lines[i+1:i+4]
                    amount=_number(nearby[0]) if nearby else None
                    if amount is None or amount>100000:continue
                    rows.append({'Société':line,'Année':2025,'Dividende net BRVM':amount,
                                 'Unité publiée':nearby[0],'Date de paiement':'',
                                 'Date ex-dividende':'','Source':url})
        except Exception as exc:errors.append(f'{type(exc).__name__}: {str(exc)[:120]}')
    return pd.DataFrame(rows).drop_duplicates(subset=['Année','Dividende net BRVM']) if rows else pd.DataFrame(),errors

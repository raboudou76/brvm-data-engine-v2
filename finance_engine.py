"""Pure financial calculations and conservative PDF extraction for BRVM RADAR."""
import io
import math
import re
import unicodedata
import pandas as pd


def parse_amount(value):
    """Parse a single localized financial number; return None for ambiguous text."""
    if value is None: return None
    s = str(value).strip().replace('\u202f','').replace('\xa0','').replace(' ', '').replace("'", '')
    if not s or s in ('-', '—', '–', 'N/A'): return None
    negative = s.startswith('(') and s.endswith(')')
    s = s.strip('()').replace('%','')
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        parts=s.split(','); s=''.join(parts) if len(parts)>2 or (len(parts)==2 and len(parts[1])==3) else s.replace(',', '.')
    elif '.' in s:
        parts=s.split('.'); s=''.join(parts) if len(parts)>2 or (len(parts)==2 and len(parts[1])==3) else s
    try:
        v=float(s)
        return -v if negative else v
    except ValueError: return None


def dcf_fcfe(initial_fcfe, growth, discount, terminal_growth, shares, years=5):
    if shares <= 0 or discount <= terminal_growth or discount <= -1 or growth <= -1 or years < 1:
        raise ValueError('Hypothèses invalides : actions > 0 et taux d’actualisation > croissance terminale.')
    flows=[initial_fcfe*(1+growth)**t for t in range(1, years+1)]
    pv=sum(v/(1+discount)**t for t,v in enumerate(flows,1))
    terminal=flows[-1]*(1+terminal_growth)/(discount-terminal_growth)
    return {'value_per_share':(pv+terminal/(1+discount)**years)/shares,'pv_flows':pv,'pv_terminal':terminal/(1+discount)**years,'terminal_share':(terminal/(1+discount)**years)/(pv+terminal/(1+discount)**years) if pv+terminal/(1+discount)**years else None}


def dividend_discount(dividend, growth, cost_equity):
    if cost_equity <= growth or dividend < 0: raise ValueError('Coût des fonds propres supérieur à la croissance, dividende positif.')
    return dividend*(1+growth)/(cost_equity-growth)


def residual_income(book_value_per_share, roe, cost_equity, growth):
    if book_value_per_share < 0 or cost_equity <= growth: raise ValueError('Valeur comptable positive et coût des fonds propres supérieur à la croissance.')
    return book_value_per_share+(roe-cost_equity)*book_value_per_share/(cost_equity-growth)


def ratios(data):
    def div(a,b): return a/b if a is not None and b not in (None,0) else None
    return {'Marge nette':div(data.get('resultat_net'),data.get('chiffre_affaires')),
            'ROE':div(data.get('resultat_net'),data.get('capitaux_propres')),
            'Dette / capitaux propres':div(data.get('dette_financiere'),data.get('capitaux_propres')),
            'FCFE (proxy)':None if data.get('flux_operationnel') is None or data.get('investissements') is None else data['flux_operationnel']-data['investissements']+(data.get('variation_dette') or 0)}


def extract_pdf(pdf_bytes, max_pages=100):
    import fitz
    if len(pdf_bytes)>25*1024*1024: raise ValueError('Fichier supérieur à 25 Mo.')
    doc=fitz.open(stream=pdf_bytes,filetype='pdf')
    if doc.page_count>max_pages:
        doc.close(); raise ValueError(f'Plus de {max_pages} pages : sélectionnez un extrait du rapport.')
    found=[]; overview=[]
    for i,page in enumerate(doc):
        text=page.get_text('text',sort=True)
        normalized=unicodedata.normalize('NFKD',text.lower())
        normalized=''.join(c for c in normalized if not unicodedata.combining(c))
        tags=[name for name,terms in {'Bilan':['bilan','total actif','total passif'], 'Résultat':['compte de resultat','resultat net','produits d exploitation'], 'Trésorerie':['flux de tresorerie','tableau des flux']}.items() if any(t in normalized for t in terms)]
        overview.append({'Page':i+1,'Section probable':', '.join(tags) or 'Non identifiée','Caractères':len(text),'Nature':'Scan/image probable' if len(text.strip())<50 else 'Texte extractible'})
        count=0
        try:
            for t in page.find_tables().tables:
                raw=t.extract()
                if raw and len(raw)>=2 and max(map(len,raw))>=2:
                    width=max(map(len,raw))
                    frame=pd.DataFrame([list(row)+[None]*(width-len(row)) for row in raw]).fillna('')
                    found.append({'page':i+1,'method':'Grille PDF','data':frame})
                    count+=1
        except (AttributeError, ValueError, RuntimeError, TypeError): pass
        # Conservative fallback: use visual text lines with 2+ numeric columns.
        if count==0 and len(text)>100:
            lines=[]
            for line in text.splitlines():
                line=' '.join(line.split())
                if len(line)>8 and re.search(r'\d',line):
                    m=re.match(r'^(.*?)(\s+[-(]?\d[\d\s.,)]*(?:\s+[-(]?\d[\d\s.,)]*)?)$',line)
                    if m and len(m.group(1))>3: lines.append([m.group(1).strip(),m.group(2).strip()])
            if len(lines)>=4:
                found.append({'page':i+1,'method':'Lignes de texte (non structurées)','data':pd.DataFrame(lines,columns=['Libellé brut','Montants bruts à vérifier'])})
    doc.close()
    return pd.DataFrame(overview),found


def tables_excel(tables):
    buffer=io.BytesIO()
    with pd.ExcelWriter(buffer,engine='openpyxl') as writer:
        for i,item in enumerate(tables[:80],1):
            item['data'].to_excel(writer,sheet_name=f"P{item['page']}_T{i}"[:31],index=False,header=False)
    return buffer.getvalue()

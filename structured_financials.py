"""Extraction prudente des comptes annuels BRVM depuis des PDF texte natifs.
Valeurs en millions de FCFA, pages/années explicitement identifiées. Sans OCR.
"""
import re
import unicodedata
import pandas as pd

METRICS = {'chiffre_affaires': 'Chiffre d’affaires', 'resultat_net': 'Résultat net', 'pnb': 'Produit net bancaire'}

def norm(s):
    s = unicodedata.normalize('NFKD', str(s).lower())
    return ' '.join(''.join(c for c in s if not unicodedata.combining(c)).split())

def _amount(value):
    v = str(value).strip().replace('\u202f',' ').replace('\xa0',' ')
    if not re.fullmatch(r'\(?-?\d[\d .]*\)?',v): return None
    negative = v.startswith('(') or v.startswith('-')
    v = v.strip('()- ').replace(' ','').replace('.','')
    try: return -float(v) if negative else float(v)
    except ValueError: return None

def _units(text):
    t=norm(text).replace('.', ' ')
    if re.search(r'(?:en\s+)?millions?\s+(?:de\s+)?(?:f\s*)?cfa\b',t): return 'millions FCFA'
    if re.search(r'(?:en\s+)?milliers?\s+(?:de\s+)?(?:f\s*)?cfa\b',t): return 'milliers FCFA'
    if re.search(r'(?:en\s+)?(?:f\s*)?cfa\b',t): return 'FCFA'
    return None

def _label_type(label):
    n=norm(label).strip(' :-')
    if re.fullmatch(r'chiffre d.affaires(?: net)?',n): return 'chiffre_affaires'
    if re.fullmatch(r'(?:produit|produits) net(?:s)? bancaire(?:s)?|pnb',n): return 'pnb'
    if re.fullmatch(r'resultat net(?: de l.exercice)?|benefice net(?: de l.exercice)?',n): return 'resultat_net'
    return None

def extract_structured(pdf_bytes):
    import fitz
    if len(pdf_bytes)>25*1024*1024: raise ValueError('PDF supérieur à 25 Mo')
    doc=fitz.open(stream=pdf_bytes,filetype='pdf')
    if doc.page_count>120: raise ValueError('PDF supérieur à 120 pages')
    candidates=[]; diagnostics=[]
    try:
        for i,page in enumerate(doc):
            txt=page.get_text('text')
            if not txt.strip():
                diagnostics.append({'Page':i+1,'Statut':'Aucun texte natif — OCR nécessaire'})
                continue
            words=page.get_text('words')
            # Group words by baseline to preserve the financial statement's horizontal columns.
            lines=[]
            for w in sorted(words,key=lambda w:(round((w[1]+w[3])/2/3),w[0])):
                y=(w[1]+w[3])/2
                if lines and abs(lines[-1][0]-y)<2.6: lines[-1][1].append(w)
                else: lines.append([y,[w]])
            year_headers=[]
            for y,ws in lines:
                years=[(int(w[4]),(w[0]+w[2])/2) for w in ws if re.fullmatch(r'20\d{2}',w[4])]
                if len(years)>=2:
                    for a in range(len(years)-1):
                        for b in range(a+1,len(years)):
                            if years[a][1] < years[b][1] and abs(years[a][0]-years[b][0])<=2:
                                year_headers.append((y,years[a],years[b]))
            if not year_headers:
                diagnostics.append({'Page':i+1,'Statut':'Colonnes annuelles non identifiées'})
                continue
            for y,ws in lines:
                for hy,(yr1,x1),(yr2,x2) in year_headers:
                    if y<=hy+2 or y>hy+page.rect.height*.72: continue
                    # For two-column balance/result pages, use only the statement's own numeric columns.
                    col_width=abs(x2-x1)
                    if col_width<18 or col_width>page.rect.width*.25: continue
                    right_words=[w for w in ws if (w[0]+w[2])/2 >= x1-col_width*.65 and (w[0]+w[2])/2 <= x2+col_width*.5]
                    if len(right_words)<2: continue
                    left=[w for w in ws if (w[0]+w[2])/2 < x1-col_width*.65]
                    label=' '.join(w[4] for w in sorted(left,key=lambda w:w[0]))
                    key=_label_type(label)
                    if not key: continue
                    # Numeric groups may contain thousands separated by spaces.
                    leftnum=' '.join(w[4] for w in sorted(right_words,key=lambda w:w[0]) if (w[0]+w[2])/2 < (x1+x2)/2)
                    rightnum=' '.join(w[4] for w in sorted(right_words,key=lambda w:w[0]) if (w[0]+w[2])/2 >= (x1+x2)/2)
                    a,b=_amount(leftnum),_amount(rightnum)
                    if a is None or b is None: continue
                    # Unit must be visible near the annual heading and in the same horizontal statement region.
                    context=' '.join(w[4] for yy,ww in lines if abs(yy-hy)<42 for w in ww if abs((w[0]+w[2])/2-x1)<page.rect.width*.45)
                    unit=_units(context) or _units(txt[:1800])
                    if unit not in ('millions FCFA','milliers FCFA','FCFA'):
                        diagnostics.append({'Page':i+1,'Statut':f'Unité non identifiable pour {label}'})
                        continue
                    scale={'millions FCFA':1,'milliers FCFA':.001,'FCFA':.000001}[unit]
                    candidates.append({'Indicateur':key,'Exercice N':yr1,'Exercice N-1':yr2,'2025 ou N':a*scale,'2024 ou N-1':b*scale,'Unité':'millions FCFA','Unité source':unit,'Page':i+1,'Libellé source':label,'Statut':'Extraction PDF — à confronter à la source'})
        frame=pd.DataFrame(candidates)
        if not frame.empty:
            frame=frame.drop_duplicates(subset=['Indicateur','Exercice N','Exercice N-1','2025 ou N','2024 ou N-1','Page'])
        return pd.DataFrame(diagnostics),frame,pd.DataFrame()
    finally: doc.close()

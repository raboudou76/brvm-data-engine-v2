"""Read-only, code-independent financial table layout extraction.
No database writes; all extracted values are candidates until reviewed.
"""
import io,re,unicodedata
import fitz
from PIL import Image

LABELS={
 'PNB':[r'produit\s+net\s+bancaire'],
 'RN':[r'(?<!avant\s)resultat\s+net(?:\s+de\s+l.?exercice)?',r'net\s+(?:income|profit)'],
 'EQUITY':[r'capitaux\s+propres(?:\s+et\s+ressources\s+assimilees)?',r'total\s+equity'],
 'ASSETS':[r'total\s+(?:de\s+l.?actif|actif|assets)'],
 'LIABILITIES_EQUITY':[r'total\s+(?:du\s+)?passif'],
 'REVENUE':[r'chiffre\s+d.?affaires',r'\b(?:total\s+)?revenues?\b'],
 'OPERATING_PROFIT':[r'resultat\s+d.?exploitation',r'operating\s+(?:profit|income)'],
 'EBITDA':[r'\bebitda\b'],
 'CASH_FLOW':[r'flux\s+net\s+de\s+tresorerie'],
}
DATE=re.compile(r'(?<!\d)(?:\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*)?(20\d{2})(?!\d)')
NUM=re.compile(r'^\(?[-−]?\d{1,3}(?:[ \u00a0\u202f.]\d{3})+(?:,\d+)?\)?$|^\(?[-−]?\d+(?:,\d+)?\)?$')

def normal(s):
 return re.sub(r'\s+',' ',unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower()).strip()

def metric_for(s):
 t=normal(s)
 for name,ps in LABELS.items():
  if any(re.search(p,t) for p in ps):return name
 return None

def number(s):
 t=s.strip().replace('\u00a0',' ').replace('\u202f',' ').replace('−','-')
 if not NUM.fullmatch(t):return None
 neg=t.startswith('(') and t.endswith(')')
 t=t.strip('()').replace(' ','').replace('.','').replace(',','.')
 try:return (-1 if neg else 1)*float(t)
 except ValueError:return None

def unit_for(s):
 t=normal(s)
 if re.search(r'\bmillions?\s+(?:de\s+)?(?:f\s*cfa|fcfa|xof)\b',t):return ('millions FCFA',1)
 if re.search(r'\bmilliers?\s+(?:de\s+)?(?:f\s*cfa|fcfa|xof)\b',t):return ('milliers FCFA',.001)
 if re.search(r'\b(?:fcfa|f\s*cfa|xof)\b',t):return ('FCFA',.000001)
 return ('Unité non confirmée',None)

def make_lines(data):
 """Tesseract line IDs preserve separated year columns; word geometry retained."""
 groups={}
 for i,t in enumerate(data['text']):
  if not t.strip() or float(data['conf'][i])<0:continue
  k=tuple(data[k][i] for k in ('block_num','par_num','line_num'))
  groups.setdefault(k,[]).append({'t':t,'x':data['left'][i],'r':data['left'][i]+data['width'][i], 'cx':data['left'][i]+data['width'][i]/2,'y':data['top'][i]+data['height'][i]/2})
 out=[]
 for ws in groups.values():
  ws.sort(key=lambda w:w['x'])
  out.append({'words':ws,'text':' '.join(w['t'] for w in ws),'y':sum(w['y'] for w in ws)/len(ws)})
 return sorted(out,key=lambda a:a['y'])

def extract_dates(lines):
 found=[]
 for ln in lines:
  ws=ln['words']
  for i in range(len(ws)):
   for n in range(1,min(5,len(ws)-i)+1):
    group=ws[i:i+n];token=''.join(w['t'] for w in group)
    match=DATE.fullmatch(token)
    if match:
     year=int(match.group(1));cx=sum(w['cx'] for w in group)/len(group)
     if not any(abs(d['x']-cx)<14 and d['year']==year for d in found):found.append({'year':year,'x':cx,'y':ln['y']})
 return found

def candidate_numbers(words):
 """Collect numeric word sequences, preferring grouped thousands; reject ambiguous joins."""
 found=[];i=0
 while i<len(words):
  w=words[i];best=None
  for n in range(min(5,len(words)-i),0,-1):
   grp=words[i:i+n]
   if n>1 and any(grp[j+1]['x']-grp[j]['r']>52 for j in range(n-1)):continue
   token=' '.join(q['t'] for q in grp)
   v=number(token)
   if v is not None:
    best=(n,{'raw':token,'value':v,'x':sum(q['cx'] for q in grp)/len(grp),'left':grp[0]['x']});break
  if best:found.append(best[1]);i+=best[0]
  else:i+=1
 return found

def extract(raw,title='',max_pages=8,dpi=220):
 import pytesseract
 doc=fitz.open(stream=raw,filetype='pdf')
 rows=[];diag={'pages':len(doc),'pages_processed':0,'headers':0,'assigned':0,'unassigned':0,'errors':[]}
 try:
  for pi in range(min(max_pages,len(doc))):
   try:
    page=doc[pi];pix=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
    im=Image.open(io.BytesIO(pix.tobytes('png')))
    langs=pytesseract.get_languages(config='');lang='fra+eng' if 'fra' in langs else 'eng'
    d=pytesseract.image_to_data(im,lang=lang,config='--psm 6',output_type=pytesseract.Output.DICT,timeout=80)
    lines=make_lines(d);dates=extract_dates(lines);diag['headers']+=len(dates);diag['pages_processed']+=1
    header_candidates=[]
    for i,a in enumerate(dates):
     for b in dates[i+1:]:
      if a['year']!=b['year'] and abs(a['y']-b['y'])<35 and abs(a['x']-b['x'])>im.width*.04:
       header_candidates.append(sorted([a,b],key=lambda x:x['x']))
    # The label and its amounts may be on different OCR lines; group by y proximity.
    for ln in lines:
     m=metric_for(ln['text'])
     if not m:continue
     near=[q for q in lines if abs(q['y']-ln['y'])<max(15,im.height*.011)]
     words=sorted([w for q in near for w in q['words']],key=lambda w:w['x'])
     # Exclude label region, and OCR row code / ordinal by using right-side numeric zone.
     numeric_zone=im.width*.55
     nums=candidate_numbers([w for w in words if w['x']>numeric_zone])
     # Some OCR line arrangements include left value inside text line; require plausible magnitude.
     nums=[n for n in nums if abs(n['value'])>=1000]
     # Flag incomplete thousand groups rather than treating them as validated.
     for n in nums:
      n['fragment_suspect']=bool(re.fullmatch(r'\d{1,3}(?: \d{3}){1,2}',n['raw']))
     # Drop duplicates from overlapping line groups.
     nums=list({(round(n['x']),n['raw']):n for n in nums}.values())
     if not nums:continue
     prev=[h for h in header_candidates if max(v['y'] for v in h)<ln['y'] and ln['y']-max(v['y'] for v in h)<im.height*.55]
     header=max(prev,key=lambda h:max(v['y'] for v in h)) if prev else None
     # Determine explicit unit only; never infer from size of numbers.
     page_top=' '.join(q['text'] for q in lines if q['y']<ln['y'] and q['y']>max(0,ln['y']-im.height*.32))
     unit,factor=unit_for(page_top)
     for n in nums:
      year=None;reason='En-têtes non associés'
      if header and len(nums)==2:
       ordered=sorted(nums,key=lambda x:x['x'])
       if ordered[1]['x']-ordered[0]['x']>im.width*.045:
        year=header[ordered.index(n)]['year'];reason='Deux colonnes, attribution géométrique provisoire'
      elif header:reason='Nombre de valeurs différent des colonnes'
      if factor is None:reason+='; unité non confirmée'
      if n['fragment_suspect']:
       reason+='; fragmentation OCR possible'
       year=None
      rows.append({'Indicateur':m,'Année proposée':year,'Montant brut':n['raw'], 'Montant numérique':n['value'], 'Unité':unit,'Millions FCFA':round(n['value']*factor,6) if factor is not None else None,'Page PDF':pi+1,'Preuve':ln['text'][:240],'Méthode':reason,'Statut':'NON CERTIFIÉ'})
      diag['assigned' if year is not None else 'unassigned']+=1
   except Exception as e:
    diag["errors"].append(f"Page {pi+1}: {type(e).__name__}: {str(e)[:140]}")
  return rows,diag
 finally:doc.close()

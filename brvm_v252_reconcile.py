"""V2.5.2: conservative, read-only OCR cell reconciliation.
Requires V2.5.1 extract_archived; never writes to database.
"""
import re
from collections import defaultdict

NUMBER = re.compile(r'(?<!\d)(?:\(?-?\d{1,3}(?:[ \u00a0\u202f]\d{3}){2,5}\)?|\(?-?\d{7,15}\)?)(?!\d)')
YEAR = re.compile(r'(?<!\d)(20(?:1\d|2\d|3\d))(?!\d)')
LABELS = {
 'EQUITY': (r'capitaux\s+propres',),
 'PNB': (r'produit\s+net\s+bancaire',),
 'CA': (r'chiffre\s+d[\'’]?affaires', r'\bventes\s+de\s+marchandises'),
 'OPERATING_PROFIT': (r'r[eé]sultat\s+d[\'’]?exploitation',r'r[eé]sultat\s+op[eé]rationnel'),
 'RN': (r'r[eé]sultat\s+net\b',r'b[eé]n[eé]fice\s+net'),
 'TOTAL_ASSETS': (r'total\s+(?:de\s+l[\'’]?)?actif',r'total\s+(?:du\s+)?bilan'),
}

def _metric(line):
 for k,patterns in LABELS.items():
  if any(re.search(p,line,re.I) for p in patterns):return k
 return None

def _unit(text):
 t=text.lower()
 for pat,label,mul in [(r'\bmillions?\s+(?:de\s+)?(?:f\.?cfa|xof)\b','millions FCFA',1000000),(r'\bmilliers?\s+(?:de\s+)?(?:f\.?cfa|xof)\b','milliers FCFA',1000),(r'\b(?:f\.?cfa|xof)\b','FCFA',1)]:
  if re.search(pat,t):return label,mul
 return 'Unité non confirmée',None

def _ocr_page(pdf_bytes,page_index,dpi=210):
 import fitz,pytesseract
 from PIL import Image
 with fitz.open(stream=pdf_bytes,filetype='pdf') as pdf:
  pix=pdf[page_index].get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
  im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
  langs=pytesseract.get_languages(config='')
  lang='fra+eng' if 'fra' in langs else 'eng'
  data=pytesseract.image_to_data(im,lang=lang,config='--psm 6',output_type=pytesseract.Output.DICT,timeout=90)
 groups=defaultdict(list)
 for i,t in enumerate(data['text']):
  if not str(t).strip():continue
  k=(data['block_num'][i],data['par_num'][i],data['line_num'][i])
  groups[k].append({'text':str(t).strip(),'x':int(data['left'][i]),'right':int(data['left'][i])+int(data['width'][i]),'y':int(data['top'][i])})
 lines=[]
 for ws in groups.values():
  ws.sort(key=lambda x:x['x']);lines.append({'words':ws,'text':' '.join(w['text'] for w in ws),'y':sum(w['y'] for w in ws)/len(ws)})
 return sorted(lines,key=lambda x:x['y']),im.width

def _cells(words,width):
 """Join OCR thousand groups only within close x-spacing; do not fabricate fragments."""
 tokens=[];i=0
 while i<len(words):
  w=words[i]; t=w['text'].strip(']|')
  if not re.fullmatch(r'\(?-?\d[\d.,]*\)?',t):i+=1;continue
  best=None
  for j in range(i+1,min(i+5,len(words))+1):
   part=words[i:j]
   if any(part[k+1]['x']-part[k]['right']>max(25,width*.017) for k in range(len(part)-1)):break
   raw=' '.join(p['text'].strip(']|') for p in part)
   if not re.fullmatch(r'\(?-?\d{1,3}(?:[ \u00a0\u202f]\d{3}){1,5}\)?|\(?-?\d{7,15}\)?',raw):continue
   best=(j,raw,part[0]['x'],part[-1]['right'])
  if best:
   j,raw,x,r=best
   try:value=int(re.sub(r'[^\d-]','',raw))
   except ValueError:i+=1;continue
   if abs(value)>9999:tokens.append({'raw':raw,'value':value,'x':(x+r)/2})
   i=j
  else:i+=1
 return tokens

def _year_headers(lines,at_y):
 headers=[]
 for ln in lines:
  if ln['y']>=at_y:break
  found=[]
  for w in ln['words']:
   m=YEAR.fullmatch(w['text'].strip('()[]'))
   if m:found.append((int(m.group(1)),(w['x']+w['right'])/2))
  if len(found)>=2:headers=found
 return headers

def reconcile(document,max_pages=8):
 """Preserve V2.5.1 results; add geometric OCR candidates, report differences."""
 from pathlib import Path
 import hashlib
 from brvm_hybrid_extractor import extract_archived
 base=extract_archived(document,max_pages)
 path=Path(document['stored_path']);raw=path.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=document['sha256']:raise ValueError('SHA-256 invalide')
 rows=[];errors=[];page_units={}
 for pi in range(base['pages_analyzed']):
  try:
   lines,width=_ocr_page(raw,pi)
   # Require unit evidence on this page, not merely a currency mention in a footer.
   units=[_unit(ln['text']) for ln in lines if re.search(r'\b(en\s+)?(?:millions?|milliers?)\s+(?:de\s+)?(?:f\.?cfa|xof)\b',ln['text'],re.I)]
   unit,mul=units[0] if len(set(units))==1 and units else ('Unité non confirmée',None)
   page_units[pi+1]=unit
   for ln in lines:
    metric=_metric(ln['text'])
    if not metric:continue
    # Include OCR line fragments at nearly identical vertical positions.
    words=sorted([w for other in lines if abs(other['y']-ln['y'])<12 for w in other['words']],key=lambda w:w['x'])
    cells=_cells([w for w in words if w['x']>width*.43],width)
    cells=list({(round(c['x']/8),c['value']):c for c in cells}.values())
    if not cells:continue
    headers=_year_headers(lines,ln['y'])
    for ix,c in enumerate(sorted(cells,key=lambda c:c['x'])):
     yr=None
     # Only assign year if two distinct header years and unique nearest geometry.
     if len({y for y,x in headers})>=2:
      nearest=sorted(headers,key=lambda h:abs(h[1]-c['x']))
      if len(nearest)>=2 and abs(nearest[0][1]-c['x'])<width*.12 and abs(nearest[0][1]-c['x'])+width*.04<abs(nearest[1][1]-c['x']):yr=nearest[0][0]
     warnings=[]
     if yr is None:warnings.append('Exercice non attribué : en-tête géométrique insuffisant')
     if mul is None:warnings.append('Unité non confirmée')
     rows.append({'indicator':metric,'fiscal_year_proposed':yr,'report_year':document.get('fiscal_year'),'period':document.get('period'),
                  'column_index':ix,'raw_amount':c['raw'],'numeric_candidate':c['value'],'unit_detected':unit,
                  'amount_millions_fcfa':round(c['value']*mul/1000000,6) if mul else None,
                  'page_pdf':pi+1,'extraction_method':'ocr-geometrie-v252','evidence':ln['text'][:400],
                  'source_url':document['source_url'],'document_id':document['id'],'status':'NON_CERTIFIE','warning':'; '.join(warnings)})
  except Exception as exc:errors.append(f'Page {pi+1}: {type(exc).__name__}: {str(exc)[:160]}')
 # Merge by page/indicator/value, never replace an existing complete candidate with a fragment.
 combined={}
 for row in base['candidates']+rows:
  key=(row['page_pdf'],row['indicator'],row['numeric_candidate'])
  if key not in combined:combined[key]=row.copy()
  else:
   old=combined[key]
   if old.get('fiscal_year_proposed') is None and row.get('fiscal_year_proposed') is not None:old['fiscal_year_proposed']=row['fiscal_year_proposed']
   if old.get('unit_detected')=='Unité non confirmée' and row.get('unit_detected')!='Unité non confirmée':
    old['unit_detected']=row['unit_detected'];old['amount_millions_fcfa']=row['amount_millions_fcfa']
 merged=list(combined.values())
 for row in merged:
  issues=[]
  if row.get('fiscal_year_proposed') is None:issues.append('Année N/N-1 non vérifiée')
  if row.get('amount_millions_fcfa') is None:issues.append('Unité non confirmée')
  if len(re.sub(r'\D','',row['raw_amount']))<8:issues.append('Fragment numérique possible')
  row['warning']='; '.join(issues) or 'Contrôle humain obligatoire'
  row['status']='NON_CERTIFIE'
 original={(r['page_pdf'],r['indicator'],r['numeric_candidate']) for r in base['candidates']}
 return {'title':base['title'],'pages_total':base['pages_total'],'pages_analyzed':base['pages_analyzed'],
         'candidates':merged,'page_diagnostics':base['page_diagnostics'],'new_candidates':sum((r['page_pdf'],r['indicator'],r['numeric_candidate']) not in original for r in merged),
         'original_count':len(base['candidates']),'page_units':page_units,'errors':errors,
         'note':'Lecture seule; années et unités attribuées uniquement si preuves explicites; aucune certification.'}

"""V2.4.3 experimental universal OCR table reconstruction.
Read-only; no accounting-code dependency, no automatic certification or database writes.
"""
import io, re, unicodedata
import fitz

ALIASES = {
 'PNB': [r'produit\s+net\s+bancaire'],
 'RN': [r'resultat\s+net(?:\s+de\s+l.?exercice)?',r'benefice\s+net',r'net\s+(?:income|profit)'],
 'EQUITY': [r'capitaux\s+propres(?:\s+et\s+ressources\s+assimilees)?',r'fonds\s+propres',r'total\s+equity'],
 'ASSETS': [r'total\s+(?:de\s+l.?actif|actif|du\s+bilan)',r'total\s+assets'],
 'LIABILITIES_EQUITY': [r'total\s+du\s+passif',r'total\s+passif'],
 'REVENUE': [r'chiffre\s+d.?affaires',r'total\s+des\s+revenus',r'\brevenue\b'],
 'OPERATING_PROFIT': [r'resultat\s+d.?exploitation',r'operating\s+profit'],
}
DATE = re.compile(r'(?<!\d)(?:(?:31|30|29|28)[/.-](?:12|06|03|09)[/.-])?(20\d{2})(?!\d)')
NUM = re.compile(r'^[-−(]?\d[\d .\u00a0\u202f]*\d(?:,\d+)?\)?$|^[-−]?\d{1,3}$')

def normal(s):
 return re.sub(r'\s+',' ',unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower()).strip()

def numeric(s):
 t=s.strip().replace('\u00a0','').replace('\u202f','').replace(' ','').replace('−','-')
 neg=t.startswith('(') and t.endswith(')')
 t=t.strip('()').replace(',','.')
 if not re.fullmatch(r'-?\d+(?:\.\d+)?',t):return None
 try: return -float(t) if neg else float(t)
 except ValueError:return None

def detect_metric(s):
 s=normal(s)
 for name,patterns in ALIASES.items():
  if any(re.search(p,s) for p in patterns):return name
 return None

def unit_detect(text):
 t=normal(text)
 patterns=[(r'\b(?:en\s+)?millions?\s+(?:de\s+)?(?:f\s*cfa|fcfa|xof)\b',1.0,'millions FCFA'),
 (r'\b(?:en\s+)?milliers?\s+(?:de\s+)?(?:f\s*cfa|fcfa|xof)\b',.001,'milliers FCFA'),
 (r'\b(?:en\s+)?(?:f\s*cfa|fcfa|xof)\b',.000001,'FCFA')]
 found=[(factor,label) for pat,factor,label in patterns[:2] if re.search(pat,t)]
 if len(found)>1:return None,'Unités contradictoires'
 if found:return found[0]
 if re.search(patterns[2][0],t):return patterns[2][1],patterns[2][2]
 return None,'Unité non indiquée'

def word_rows(data):
 # Word geometry; avoid relying on OCR paragraph/line numbers, which frequently merge table columns.
 words=[]
 for i,s in enumerate(data['text']):
  s=s.strip()
  if not s:continue
  if float(data['conf'][i])<0:continue
  words.append({'s':s,'x':data['left'][i],'cx':data['left'][i]+data['width'][i]/2,'y':data['top'][i]+data['height'][i]/2,'h':data['height'][i],'w':data['width'][i]})
 words.sort(key=lambda w:w['y'])
 rows=[]
 for w in words:
  # tolerance scales with height, but keep distinct adjacent table rows
  if rows and abs(rows[-1]['y']-w['y'])<=max(6,min(13,w['h']*.52)):
   r=rows[-1];r['words'].append(w);r['y']=sum(q['y'] for q in r['words'])/len(r['words'])
  else:rows.append({'y':w['y'],'words':[w]})
 for r in rows:
  r['words'].sort(key=lambda w:w['x']);r['text']=' '.join(w['s'] for w in r['words'])
 return rows

def date_anchors(rows):
 result=[]
 for r in rows:
  ws=r['words']
  for i,w in enumerate(ws):
   # Dates may be split across OCR words, e.g. 31/12/ 2025
   for count in (1,2,3):
    segment=ws[i:i+count]
    if len(segment)!=count:continue
    token=''.join(v['s'] for v in segment)
    m=DATE.fullmatch(token)
    if m:
     yr=int(m.group(1));x=sum(v['cx'] for v in segment)/len(segment)
     result.append({'year':yr,'x':x,'y':r['y'],'token':token})
 return result

def number_cells(words, min_x):
 """Build numeric cells from separated groups of OCR words; never guess if ambiguous."""
 ws=[w for w in words if w['x']>=min_x]
 groups=[]
 for w in ws:
  if not groups or w['x']-(groups[-1][-1]['x']+groups[-1][-1]['w'])>max(16,w['h']*1.9):groups.append([w])
  else:groups[-1].append(w)
 cells=[]
 for g in groups:
  token=' '.join(w['s'] for w in g).strip()
  if not NUM.fullmatch(token):continue
  value=numeric(token)
  if value is None:continue
  cells.append({'raw':token,'value':value,'x':sum(w['cx'] for w in g)/len(g)})
 return cells

def reconstruct(raw,title='',max_pages=8,dpi=210):
 import pytesseract
 from PIL import Image
 doc=fitz.open(stream=raw,filetype='pdf')
 out=[];diag={'pages':len(doc),'pages_ocr':0,'errors':[],'rows':0,'assigned':0,'unassigned':0,'headers_found':0}
 try:
  for pn in range(min(len(doc),max_pages)):
   page=doc[pn]
   try:
    pix=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes('png')))
    langs=pytesseract.get_languages(config='')
    lang='fra+eng' if 'fra' in langs else 'eng'
    data=pytesseract.image_to_data(img,lang=lang,config='--psm 6',output_type=pytesseract.Output.DICT,timeout=75)
    diag['pages_ocr']+=1
   except Exception as e:
    diag['errors'].append(f'Page {pn+1}: {type(e).__name__}: {str(e)[:160]}');continue
   rows=word_rows(data); anchors=date_anchors(rows)
   diag['headers_found']+=len(anchors)
   whole=' '.join(r['text'] for r in rows[:20]);factor,unit=unit_detect(whole)
   for ri,row in enumerate(rows):
    metric=detect_metric(row['text'])
    if not metric:continue
    # Find a recent preceding header with two distinct year anchors on one row.
    earlier=[a for a in anchors if 0<row['y']-a['y']<min(460,img.height*.48)]
    by_y={}
    for a in earlier:by_y.setdefault(round(a['y']/14),[]).append(a)
    valid=[v for v in by_y.values() if len(set(a['year'] for a in v))>=2]
    header=max(valid,key=lambda v:sum(a['y'] for a in v)/len(v)) if valid else []
    header=sorted(header,key=lambda a:a['x'])
    # Separate label and numeric region. Numeric columns usually lie to the right of label.
    label_words=[w for w in row['words'] if not re.fullmatch(r'[-−]?\d[\d .]*',w['s'])]
    label_end=max((w['x']+w['w'] for w in label_words),default=img.width*.25)
    cells=number_cells(row['words'],max(label_end+3,img.width*.30))
    # OCR can join two cells: leave unassigned rather than splitting an unknown string.
    for cell in cells:
     year=None;reason='En-têtes annuels non détectés'
     if len(header)==2 and len(cells)==2:
      # Geometric ordering and column separation required.
      ordered=sorted(cells,key=lambda c:c['x'])
      spacing=abs(ordered[1]['x']-ordered[0]['x'])
      if spacing>img.width*.055 and header[0]['x']<header[1]['x']:
       ix=ordered.index(cell)
       year=header[ix]['year'];reason='Ordre spatial de deux colonnes sous en-têtes (provisoire)'
      else:reason='Colonnes trop rapprochées ou ambiguës'
     elif len(header)==2:reason='Nombre de montants différent du nombre de colonnes'
     if factor is None:reason+=' ; unité non confirmée'
     diag['assigned' if year is not None else 'unassigned']+=1
     out.append({'Indicateur':metric,'Année attribuée (provisoire)':year,'Montant OCR brut':cell['raw'],
       'Montant numérique candidat':cell['value'],'Unité détectée':unit,
       'Millions FCFA (provisoire)':round(cell['value']*factor,6) if factor is not None else None,
       'Page PDF':pn+1,'Ligne source':row['text'][:240],
       'En-têtes':str([(a['year'],round(a['x'])) for a in header]),
       'Contrôle':reason,'Statut':'NON CERTIFIÉ'})
     diag['rows']+=1
  return out,diag
 finally:doc.close()

"""V2.4.5: read-only geometry-aware OCR financial cell reconstruction.
No hardcoded issuer values, no database writes, no automatic certification.
"""
import io, re, unicodedata
import fitz
from PIL import Image
from brvm_layout_engine import LABELS, metric_for, normal, unit_for

YEAR = re.compile(r'(?<!\d)(20\d{2})(?!\d)')
GROUP = re.compile(r'^\(?-?\d{1,3}(?:[ \u00a0\u202f.]\d{3}){1,5}\)?$')
DIGITS = re.compile(r'^\(?-?\d+(?:[.,]\d+)?\)?$')

def ocr_lines(image, lang):
 import pytesseract
 data=pytesseract.image_to_data(image,lang=lang,config='--psm 6',output_type=pytesseract.Output.DICT,timeout=90)
 lines={}
 for i,t in enumerate(data['text']):
  t=t.strip()
  if not t or float(data['conf'][i])<0:continue
  key=tuple(data[k][i] for k in ('block_num','par_num','line_num'))
  w={'t':t,'x':data['left'][i],'right':data['left'][i]+data['width'][i], 'y':data['top'][i]+data['height'][i]/2}
  lines.setdefault(key,[]).append(w)
 return [{'words':sorted(ws,key=lambda w:w['x']), 'text':' '.join(w['t'] for w in sorted(ws,key=lambda w:w['x'])), 'y':sum(w['y'] for w in ws)/len(ws)} for ws in lines.values()]

def numeric_cells(words, image_width):
 """Group adjacent thousand chunks; separate columns by physical whitespace.
 Do not accept incomplete groups as reliable, nor join across large gaps.
 """
 cells=[]; i=0
 while i<len(words):
  w=words[i]
  if not re.fullmatch(r'\(?-?\d[\d.,]*[\]\|]?\)?',w['t']):i+=1;continue
  best=None
  for j in range(i+1,min(len(words),i+4)+1):
   part=words[i:j]
   if any(part[k+1]['x']-part[k]['right']>max(25,image_width*.017) for k in range(len(part)-1)):break
   raw=' '.join(p['t'].strip(']|') for p in part)
   if GROUP.fullmatch(raw) or (len(part)==1 and DIGITS.fullmatch(raw)):
    if len(part)>1 or len(re.sub(r'\D','',raw))>=5:
     best=(j,raw,part[0]['x'],part[-1]['right'])
  if best:
   j,raw,left,right=best
   digits=re.sub(r'[^\d-]','',raw)
   try:val=int(digits)
   except ValueError:i+=1;continue
   cells.append({'raw':raw,'value':val,'left':left,'right':right,'cx':(left+right)/2,'groups':j-i})
   i=j
  else:i+=1
 return cells

def headers_for(lines,width):
 headers=[]
 for ln in lines:
  matches=[]
  for w in ln['words']:
   m=YEAR.search(w['t'])
   if m:matches.append({'year':int(m.group(1)),'x':(w['x']+w['right'])/2,'y':ln['y']})
  if len(matches)>=2:
   for a in matches:
    if not any(h['year']==a['year'] and abs(h['x']-a['x'])<20 for h in headers):headers.append(a)
 return headers

def extract(raw,title='',max_pages=8,dpi=230):
 import pytesseract
 doc=fitz.open(stream=raw,filetype='pdf')
 rows=[];diag={'pages':len(doc),'pages_processed':0,'headers':0,'assigned':0,'unassigned':0,'errors':[],'mode':'OCR cell grouping; read only'}
 try:
  languages=pytesseract.get_languages(config='')
  lang='fra+eng' if 'fra' in languages else 'eng'
  for pi in range(min(len(doc),max_pages)):
   try:
    page=doc[pi]
    pix=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
    image=Image.open(io.BytesIO(pix.tobytes('png')))
    lines=ocr_lines(image,lang);diag['pages_processed']+=1
    heads=headers_for(lines,image.width);diag['headers']+=len(heads)
    for ln in lines:
     indicator=metric_for(ln['text'])
     if not indicator:continue
     # Use same-row and close OCR-line fragments, but never join unrelated lines.
     nearby=[w for q in lines if abs(q['y']-ln['y'])<max(13,image.height*.006) for w in q['words']]
     nearby.sort(key=lambda w:w['x'])
     # Exclude label text, row numbers, and the left code region.
     cells=numeric_cells([w for w in nearby if w['x']>image.width*.46],image.width)
     cells=[c for c in cells if abs(c['value'])>=1000]
     cells=list({(c['left'],c['raw']):c for c in cells}.values())
     if not cells:continue
     previous=[h for h in heads if h['y']<ln['y'] and ln['y']-h['y']<image.height*.70]
     years=sorted({h['year'] for h in previous})
     columns=sorted(previous,key=lambda h:h['x'])
     # Only use geometrical year matching when header count and cell count agree.
     unique_columns=[]
     for h in columns:
      if not any(abs(h['x']-v['x'])<image.width*.035 for v in unique_columns):unique_columns.append(h)
     if len(unique_columns)!=len(cells):unique_columns=[]
     # Header may not appear in OCR; never infer the year from document title.
     unit_text=' '.join(q['text'] for q in lines if q['y']<ln['y'] and ln['y']>max(0,ln['y']-image.height*.40))
     unit,factor=unit_for(unit_text)
     ordered=sorted(cells,key=lambda c:c['cx'])
     for ix,c in enumerate(ordered):
      year=None;issues=[]
      if unique_columns:
       head=unique_columns[ix]
       if abs(head['x']-c['cx'])<image.width*.16:year=head['year']
       else:issues.append('en-tête éloigné de la cellule')
      else:issues.append('colonnes annuelles non résolues')
      if factor is None:issues.append('unité non confirmée')
      if len(re.sub(r'\D','',c['raw']))<8:issues.append('montant possiblement fragmenté')
      rows.append({'Indicateur':indicator,'Année proposée':year,'Montant brut':c['raw'],'Montant numérique':c['value'],'Unité':unit,'Millions FCFA':round(c['value']*factor,6) if factor is not None else None,'Page PDF':pi+1,'Preuve':ln['text'][:240],'Contrôles':'; '.join(issues) if issues else 'à vérifier visuellement','Statut':'NON CERTIFIÉ'})
      diag['assigned' if year is not None else 'unassigned']+=1
   except Exception as exc:diag['errors'].append(f'Page {pi+1}: {type(exc).__name__}: {str(exc)[:150]}')
  return rows,diag
 finally:doc.close()

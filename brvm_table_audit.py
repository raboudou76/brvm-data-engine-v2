"""V2.4.2: conservative positional OCR audit. No automatic certification or database writes."""
import io, re, unicodedata
import fitz

METRICS = {
 'PNB': (r'produit\s+net\s+bancaire',),
 'RN': (r'resultat\s+net',),
 'EQUITY': (r'capitaux\s+propres',),
 'ASSETS': (r'total\s+(?:de\s+l.?actif|actif|du\s+bilan)',),
 'CA': (r'chiffre\s+d.?affaires',),
}
NUMBER = re.compile(r'(?<!\w)[(−-]?(?:\d{1,3}(?:[ .\u00a0\u202f]\d{3})+|\d{4,15})(?:,\d+)?\)?(?!\w)')
YEAR = re.compile(r'(?<!\d)20\d{2}(?!\d)')

def normalize(s):
 s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
 return re.sub(r'\s+',' ',s).strip()

def parse_amount(s):
 x=s.strip().replace('\u00a0','').replace('\u202f','').replace(' ','').replace('−','-')
 negative=x.startswith('(') and x.endswith(')')
 x=x.strip('()').replace('.','').replace(',','.')
 try:return -float(x) if negative else float(x)
 except ValueError:return None

def unit_for_page(text):
 t=normalize(text)
 matches=list(re.finditer(r'\b(?:en\s+)?(millions?|milliers?)\s+(?:de\s+)?(?:f\s*cfa|fcfa|xof)\b',t))
 if not matches:return None,'Unité non explicitement identifiée'
 kinds=set('million' if 'million' in m.group() else 'millier' for m in matches)
 if len(kinds)!=1:return None,'Unités contradictoires sur la page'
 return (1 if 'million' in kinds else 0.001),'Unité détectée dans la page (à confirmer)'

def extract_table_evidence(raw,title,max_pages=8,dpi=190):
 """Use OCR bounding boxes to infer year columns; only assign where header geometry is clear."""
 import pytesseract
 from PIL import Image
 doc=fitz.open(stream=raw,filetype='pdf')
 rows=[]; diagnostics={'pages':len(doc),'pages_examined':0,'ocr_errors':[],'unassigned':0,'assigned':0}
 title_years=[int(y) for y in YEAR.findall(title)]
 presumed=title_years[-1] if title_years else None
 try:
  for pageno in range(min(len(doc),max_pages)):
   page=doc[pageno]; diagnostics['pages_examined']+=1
   try:
    pix=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes('png')))
    langs=pytesseract.get_languages(config='')
    lang='fra+eng' if 'fra' in langs else 'eng'
    data=pytesseract.image_to_data(img,lang=lang,config='--psm 6',output_type=pytesseract.Output.DICT,timeout=60)
   except Exception as exc:
    diagnostics['ocr_errors'].append(f'Page {pageno+1}: {type(exc).__name__}: {str(exc)[:160]}');continue
   grouped={}
   for i,word in enumerate(data['text']):
    if not word.strip():continue
    key=(data['block_num'][i],data['par_num'][i],data['line_num'][i])
    grouped.setdefault(key,[]).append((data['left'][i],data['top'][i],data['width'][i],word.strip()))
   lines=[]
   for parts in grouped.values():
    parts.sort(key=lambda x:x[0]);text=' '.join(p[3] for p in parts)
    lines.append({'text':text,'norm':normalize(text),'y':sum(p[1] for p in parts)/len(parts),'words':parts})
   lines.sort(key=lambda x:x['y'])
   whole=' '.join(x['text'] for x in lines)
   factor,unit_note=unit_for_page(whole)
   # Column anchors are inferred from the closest header ABOVE a metric row.
   for j,line in enumerate(lines):
    metric=next((k for k,patterns in METRICS.items() if any(re.search(pattern,line['norm']) for pattern in patterns)),None)
    if not metric:continue
    header=[]
    for h in lines[max(0,j-22):j]:
     years=[(int(m.group()),m.start()) for m in YEAR.finditer(h['text'])]
     if len(years)>=2:
      # Map character offset to nearest OCR word bounding box.
      tokens=[];cursor=0
      for left,top,width,word in h['words']:
       pos=h['text'].find(word,cursor);cursor=max(cursor,pos+len(word))
       if YEAR.fullmatch(word):tokens.append((int(word),left+width/2))
      if len(tokens)>=2:header=tokens
    # Extract tokens in a row by matching contiguous number-shaped OCR words.
    # Do not merge neighboring values unless a clear single grouping is present.
    candidates=[]
    for m in NUMBER.finditer(line['text']):
     val=parse_amount(m.group());
     if val is None:continue
     # Character offsets are approximate, so use proportional x only as fallback.
     idx=m.start();charpos=0;xs=[]
     for left,top,width,word in line['words']:
      start=line['text'].find(word,charpos);charpos=max(charpos,start+len(word))
      if start<=idx<start+len(word):xs.append(left+width/2)
     candidates.append((m.group(),val,xs[0] if xs else None))
    # Remove code-like labels and year tokens from the numeric tail where possible.
    for raw_number,val,x in candidates:
     eligible=bool(header and x is not None and len(set(y for y,_ in header))==len(header))
     assigned_year=None;reason='En-têtes de colonnes non établis'
     if eligible:
      ordered=sorted(header,key=lambda a:a[1]);nearest=sorted(ordered,key=lambda a:abs(a[1]-x))
      if len(nearest)>=2 and abs(nearest[0][1]-x)<abs(nearest[1][1]-x)*0.65:
       assigned_year=nearest[0][0];reason='Association géométrique indicative, contrôle visuel requis'
      else:reason='Montant entre deux colonnes : attribution ambiguë'
     if factor is None:reason+=' ; unité non confirmée'
     if assigned_year is None:diagnostics['unassigned']+=1
     else:diagnostics['assigned']+=1
     rows.append({'Indicateur':metric,'Année attribuée (provisoire)':assigned_year,
      'Année du rapport':presumed,'Montant OCR brut':raw_number,
      'Montant numérique candidat':val,'Facteur vers millions FCFA':factor,
      'Millions FCFA (provisoire)':round(val*factor,6) if factor is not None else None,
      'Page PDF':pageno+1,'En-têtes détectés':str(header),
      'Ligne source':line['text'][:240],'Contrôle':reason,
      'Statut':'NON CERTIFIÉ'})
  return rows,diagnostics
 finally:doc.close()

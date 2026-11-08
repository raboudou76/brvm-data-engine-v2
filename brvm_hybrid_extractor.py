"""V2.5.1: read-only hybrid PDF extraction from V2.5.0 archives.
Candidates are NOT verified financial facts. No writes to financials or valuation.
"""
import hashlib
import io
import re
from pathlib import Path

from sqlalchemy import text
from brvm_pipeline import engine_for

LABELS = {
    'PNB': (r'produit\s+net\s+bancaire',),
    'CA': (r'chiffre\s+d[’\']?affaires', r'\bventes\s+(?:de\s+)?marchandises\b'),
    'RN': (r'\br[eé]sultat\s+net\b', r'\bb[eé]n[eé]fice\s+net\b'),
    'OPERATING_PROFIT': (r'r[eé]sultat\s+d[’\']?exploitation', r'r[eé]sultat\s+op[eé]rationnel'),
    'EQUITY': (r'capitaux\s+propres(?:\s+et\s+ressources\s+assimil[eé]es)?',),
    'TOTAL_ASSETS': (r'total\s+(?:de\s+l[’\']?)?actif', r'total\s+(?:du\s+)?bilan'),
}
# Strict groups: 1 234 567 890, or 1234567890; never concatenate arbitrary OCR fragments.
AMOUNT = re.compile(r'(?<![\d])(?:\(?-?\d{1,3}(?:[ \u00a0\u202f]\d{3}){1,5}\)?|\(?-?\d{4,15}\)?)(?!\d)')
YEAR = re.compile(r'\b20(?:1\d|2\d|3\d)\b')

def _clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('\u00a0',' ').replace('\u202f',' ')).strip()

def _unit(page_text):
    t=_clean(page_text).lower()
    for pattern,unit,mult in [
        (r'(?:en\s+)?millions\s+(?:de\s+)?(?:f\.?cfa|xof)', 'millions FCFA', 1_000_000),
        (r'(?:en\s+)?milliers\s+(?:de\s+)?(?:f\.?cfa|xof)', 'milliers FCFA', 1_000),
        (r'(?:en\s+)?(?:f\.?cfa|xof)\b', 'FCFA', 1),
    ]:
        if re.search(pattern,t,re.I):return unit,mult
    return 'Unité non confirmée',None

def _metric(line):
    for metric,patterns in LABELS.items():
        if any(re.search(p,line,re.I) for p in patterns):return metric
    return None

def _numbers(line):
    out=[]
    for m in AMOUNT.finditer(line):
        s=m.group().strip()
        raw=s.replace(' ','').replace('\u00a0','').replace('\u202f','')
        neg=raw.startswith('(') and raw.endswith(')')
        try: n=int(raw.strip('()')) * (-1 if neg else 1)
        except ValueError:continue
        # skip row numbering and year tokens
        if abs(n)<1000 or 2010<=abs(n)<=2039:continue
        out.append((s,n,m.start()))
    return out

def _candidate_rows(lines, year, period, page, method, unit, multiplier, source, docid):
    candidates=[]
    for line in lines:
        line=_clean(line)
        metric=_metric(line)
        if not metric:continue
        nums=_numbers(line)
        # only values after metric's label, where possible; conservative cap to avoid unrelated table rows
        if not nums:continue
        for index,(raw,n,position) in enumerate(nums[:3]):
            candidates.append({
                'document_id':docid,'source_url':source,'page_pdf':page,
                'indicator':metric,'fiscal_year_proposed':None,
                'report_year':year,'period':period,
                'column_index':index,'raw_amount':raw,'numeric_candidate':n,
                'unit_detected':unit,'amount_millions_fcfa':round(n*multiplier/1_000_000,6) if multiplier else None,
                'extraction_method':method,'evidence':line[:400],
                'status':'NON_CERTIFIE',
                'warning':'Année de colonne N/N-1 non vérifiée; unité non confirmée' if multiplier is None else 'Année de colonne N/N-1 non vérifiée',
            })
    return candidates

def _extract_page(page, page_no, year, period, source, docid):
    text=page.extract_text(layout=False) or ''
    tables=[]
    try:tables=page.extract_tables() or []
    except Exception:pass
    lines=[]
    for table in tables:
        for row in table:
            if row:
                lines.append(' | '.join(_clean(c) for c in row if c))
    # fallback native text if table rows are unavailable
    if not lines:lines=text.splitlines()
    unit,multiplier=_unit(text)
    if len(text.strip())>=50:
        return _candidate_rows(lines,year,period,page_no,'pdfplumber-table' if tables else 'pdfplumber-text',unit,multiplier,source,docid), 'native',len(text)
    # OCR only for scanned/near-empty pages; avoid repeated OCR on native text
    try:
        import fitz
        import pytesseract
        from PIL import Image
        with fitz.open(stream=page.pdf.stream.getvalue(),filetype='pdf') as doc:
            pix=doc[page_no-1].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
            img=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            ocr=pytesseract.image_to_string(img,lang='fra',config='--psm 6')
        unit,multiplier=_unit(ocr)
        return _candidate_rows(ocr.splitlines(),year,period,page_no,'tesseract-ocr',unit,multiplier,source,docid),'ocr',len(ocr)
    except Exception as exc:
        return [],'ocr_unavailable',f'{type(exc).__name__}: {str(exc)[:120]}'

def archived_documents(symbol,database_url=None):
    engine=engine_for(database_url)
    with engine.connect() as c:
        rs=c.execute(text('''SELECT id,symbol,title,fiscal_year,period,source_url,stored_path,sha256,byte_size
          FROM pipeline_publications WHERE symbol=:s AND status='downloaded' ORDER BY fiscal_year DESC,title'''),{'s':symbol.upper()})
        return [dict(x) for x in rs.mappings()]

def extract_archived(document,max_pages=8):
    """Read archive, verify hash and produce auditable unapproved candidates."""
    import pdfplumber
    path=Path(document['stored_path'])
    if not path.is_file():raise FileNotFoundError(f'Archive indisponible: {path}')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=document['sha256']:
        raise ValueError('Empreinte SHA-256 différente de celle enregistrée : fichier rejeté')
    if not data.startswith(b'%PDF-'):raise ValueError('Fichier non PDF')
    candidates=[];pages=[]
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for idx,page in enumerate(pdf.pages[:max(1,min(int(max_pages),30))],1):
            values,method,detail=_extract_page(page,idx,document.get('fiscal_year'),document.get('period'),document['source_url'],document['id'])
            candidates.extend(values)
            pages.append({'page':idx,'method':method,'characters_or_error':detail,'candidates':len(values)})
        total=len(pdf.pages)
    return {'title':document['title'],'pages_total':total,'pages_analyzed':len(pages),'candidates':candidates,'page_diagnostics':pages,
            'note':'Extraction exploratoire : années des colonnes, unités et chiffres non certifiés.'}

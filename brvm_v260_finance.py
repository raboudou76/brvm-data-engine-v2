"""BRVM Data Engine 2.6.0: audited CSV ingestion, SQLite persistence and cautious analytics.
No network calls, no automatic certification. All monetary figures are stored as XOF
with explicit provenance for inferred currency and scale.
"""
import csv
import io
import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ALLOWED={'BOAB','SMBC','SNTS','SGBC','CBIBF'}
INDICATORS={'EQUITY','PNB','OPERATING_PROFIT','RN','CA','DIV_NET','EPS'}
SCHEMA='''CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, indicator TEXT NOT NULL,
 fiscal_year INTEGER, period TEXT NOT NULL, raw_amount TEXT NOT NULL,
 amount_xof TEXT, currency TEXT NOT NULL, currency_basis TEXT NOT NULL,
 scale_basis TEXT NOT NULL, source_url TEXT, source_page INTEGER,
 evidence TEXT, extraction_method TEXT, review_status TEXT NOT NULL,
 issue TEXT, imported_at TEXT NOT NULL,
 UNIQUE(symbol,indicator,fiscal_year,period,raw_amount,source_url,source_page)
)'''

def connect_db(path='brvm_v260.sqlite3'):
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    db.execute(SCHEMA)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_observation ON observations(symbol,indicator,COALESCE(fiscal_year,-1),period,raw_amount,COALESCE(source_url,''),COALESCE(source_page,-1))")
    db.commit()
    return db

def _year(value):
    try:
        y=int(float(value))
        return y if 2000<=y<=2040 else None
    except (ValueError,TypeError):return None

def _amount(value):
    s=str(value or '').strip().replace('\u202f','').replace(' ','')
    if not s or not s.lstrip('-').isdigit():return None
    try:
        n=Decimal(s)
        return n if n.is_finite() and abs(n)<Decimal('1e17') else None
    except InvalidOperation:return None

def normalize(row,symbol,assume_xof=True,assume_units=True):
    indicator=str(row.get('indicator') or '').strip().upper()
    if indicator not in INDICATORS:return None,'Indicateur inconnu'
    raw=str(row.get('numeric_candidate') or row.get('raw_amount') or '').strip()
    amount=_amount(raw)
    if amount is None:return None,'Montant non numérique, absent ou hors limite'
    source=str(row.get('source_url') or '').strip()
    page=_year(row.get('page_pdf')) if False else None
    try:page=int(float(row.get('page_pdf')))
    except (ValueError,TypeError):pass
    year=_year(row.get('fiscal_year_proposed'))
    period=str(row.get('period') or 'ANNUEL').strip().upper()
    explicit_unit=str(row.get('unit_detected') or '').upper()
    explicit_conf=str(row.get('unit_confidence') or '').upper()
    explicit=explicit_conf.startswith('MENTION_EXPLICITE')
    scale=Decimal(1); scale_basis='HYPOTHESE_UNITE_ENTIERE' if assume_units else 'NON_DETERMINEE'
    if explicit:
        if 'MILLION' in explicit_unit:scale=Decimal(1000000);scale_basis='MENTION_EXPLICITE_MILLIONS'
        elif 'MILLIER' in explicit_unit:scale=Decimal(1000);scale_basis='MENTION_EXPLICITE_MILLIERS'
        elif any(k in explicit_unit for k in ('FCFA','XOF','CFA')):scale_basis='MENTION_EXPLICITE_UNITES'
    currency='XOF' if assume_xof or explicit else 'INCONNUE'
    currency_basis='CONTEXTE_UEMOA' if assume_xof and not explicit else ('MENTION_EXPLICITE' if explicit else 'NON_DETERMINEE')
    issues=[]
    if year is None:issues.append('EXERCICE_NON_ATTRIBUE')
    if not source:issues.append('SOURCE_ABSENTE')
    if str(row.get('year_confidence') or '').startswith('CONFLIT'):issues.append('CONFLIT_EXERCICE')
    if 'REJET' in str(row.get('validation_status') or '').upper():issues.append('REJET_PRECEDENT')
    if not assume_units and not explicit:issues.append('ECHELLE_INCONNUE')
    if not assume_xof and not explicit:issues.append('DEVISE_INCONNUE')
    if 'inference' in str(row.get('extraction_method') or '').lower():issues.append('OCR_RECONSTRUIT')
    if 'OCR' in str(row.get('review_reason') or '').upper():issues.append('OCR_A_REVOIR')
    blocking=any(x in issues for x in ('EXERCICE_NON_ATTRIBUE','SOURCE_ABSENTE','CONFLIT_EXERCICE','REJET_PRECEDENT','ECHELLE_INCONNUE','DEVISE_INCONNUE'))
    status='BLOQUE' if blocking else 'PROVISOIRE'
    return dict(symbol=symbol,indicator=indicator,fiscal_year=year,period=period,
      raw_amount=raw,amount_xof=str(amount*scale) if scale_basis!='NON_DETERMINEE' else None,
      currency=currency,currency_basis=currency_basis,scale_basis=scale_basis,
      source_url=source,source_page=page,evidence=str(row.get('evidence') or row.get('header_evidence') or ''),
      extraction_method=str(row.get('extraction_method') or ''),review_status=status,
      issue=';'.join(issues),imported_at=datetime.now(timezone.utc).isoformat()),None

def import_csv(db, csv_bytes, symbol, assume_xof=True, assume_units=True):
    if symbol not in ALLOWED:raise ValueError('Société non autorisée pour le pilote')
    content=csv_bytes.decode('utf-8-sig') if isinstance(csv_bytes,bytes) else csv_bytes
    rows=list(csv.DictReader(io.StringIO(content)))
    if len(rows)>10000:raise ValueError('Fichier trop volumineux')
    stats={'read':len(rows),'inserted':0,'duplicates':0,'rejected':0,'blocked':0,'provisional':0,'errors':[]}
    fields=['symbol','indicator','fiscal_year','period','raw_amount','amount_xof','currency','currency_basis','scale_basis','source_url','source_page','evidence','extraction_method','review_status','issue','imported_at']
    for index,row in enumerate(rows,2):
        obj,error=normalize(row,symbol,assume_xof,assume_units)
        if error:
            stats['rejected']+=1;stats['errors'].append(f'Ligne {index}: {error}');continue
        try:
            before=db.total_changes
            db.execute('INSERT OR IGNORE INTO observations ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',[obj[f] for f in fields])
            if db.total_changes==before:stats['duplicates']+=1
            else:
                stats['inserted']+=1
                stats['blocked' if obj['review_status']=='BLOQUE' else 'provisional']+=1
        except sqlite3.Error as exc:stats['rejected']+=1;stats['errors'].append(f'Ligne {index}: {exc}')
    db.commit()
    return stats

def observations(db,symbol):
    return [dict(x) for x in db.execute('SELECT * FROM observations WHERE symbol=? ORDER BY fiscal_year,indicator,id',(symbol,))]

def indicators(db,symbol):
    """Use only unique provisional annual figures; never average conflicting values."""
    rows=[r for r in observations(db,symbol) if r['review_status']=='PROVISOIRE' and r['fiscal_year'] and r['period']=='ANNUEL']
    grouped={}
    for r in rows:grouped.setdefault((r['fiscal_year'],r['indicator']),[]).append(r)
    unique={}
    for key,rs in grouped.items():
        vals={r['amount_xof'] for r in rs}
        if len(vals)==1 and None not in vals:unique[key]=Decimal(next(iter(vals)))
    out=[]
    for (year,metric),value in sorted(unique.items()):
        prev=unique.get((year-1,metric));growth=None
        if prev is not None and prev!=0:growth=round(float((value/prev-1)*100),2)
        out.append({'year':year,'indicator':metric,'amount_millions_xof':round(float(value/Decimal(1000000)),3),
                    'growth_pct':growth,'quality':'PROVISOIRE — NON CERTIFIÉ'})
    return out

"""Independent, auditable BRVM financial store. Python stdlib only."""
import csv, io, os, sqlite3
from datetime import datetime, timezone

SCHEMA='''CREATE TABLE IF NOT EXISTS financials (
 symbol TEXT NOT NULL, year INTEGER NOT NULL, metric TEXT NOT NULL,
 value REAL NOT NULL, unit TEXT NOT NULL, source_url TEXT NOT NULL,
 source_page TEXT NOT NULL DEFAULT '', source_title TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('a_verifier','verifie')),
 updated_at TEXT NOT NULL, PRIMARY KEY(symbol,year,metric)
);'''
METRICS={'CA','PNB','RN','DIV_NET','DIV_BRUT'}
FIELDS=['symbol','year','metric','value','unit','source_url','source_page','source_title']

def connect(path=None):
    path=path or os.environ.get('BRVM_DATA_DB','brvm_financials.sqlite3')
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    db.execute(SCHEMA)
    db.commit()
    return db

def validate(row):
    symbol=str(row.get('symbol','')).strip().upper()
    if not symbol or len(symbol)>16 or not symbol.replace('-','').isalnum(): raise ValueError('Symbole invalide')
    year=int(row.get('year'))
    if not 1990<=year<=datetime.now().year: raise ValueError('Exercice invalide')
    metric=str(row.get('metric','')).strip().upper()
    if metric not in METRICS: raise ValueError('Indicateur non reconnu')
    value=float(str(row.get('value','')).replace(',','.').replace(' ',''))
    import math
    if not math.isfinite(value): raise ValueError('Montant non fini')
    unit=str(row.get('unit','')).strip()
    if unit != ('FCFA/action' if metric.startswith('DIV_') else 'millions FCFA'):
        raise ValueError('Unité incorrecte pour cet indicateur')
    source_url=str(row.get('source_url','')).strip()
    if not source_url.startswith('https://'): raise ValueError('URL source HTTPS obligatoire')
    return (symbol,year,metric,value,unit,source_url,str(row.get('source_page','')).strip(),str(row.get('source_title','')).strip())

def stage(db,rows):
    inserted=0; errors=[]
    for idx,row in enumerate(rows,1):
        try:
            record=validate(row)
            existing=db.execute('SELECT value,status FROM financials WHERE symbol=? AND year=? AND metric=?',record[:3]).fetchone()
            if existing and (existing['status']=='verifie' or existing['value'] != record[3]):
                errors.append(f'Ligne {idx}: valeur existante, revue manuelle nécessaire; aucune écrasement');continue
            db.execute('INSERT OR REPLACE INTO financials VALUES (?,?,?,?,?,?,?,?,?,?)',record+('a_verifier',datetime.now(timezone.utc).isoformat()))
            inserted+=1
        except Exception as exc: errors.append(f'Ligne {idx}: {exc}')
    db.commit();return inserted,errors

def approve(db,symbol,year,metric):
    cur=db.execute("UPDATE financials SET status='verifie', updated_at=? WHERE symbol=? AND year=? AND metric=?",(datetime.now(timezone.utc).isoformat(),symbol,year,metric))
    db.commit();return cur.rowcount

def records(db,symbol=None,verified_only=False):
    q='SELECT * FROM financials WHERE 1=1';args=[]
    if symbol:q+=' AND symbol=?';args.append(symbol)
    if verified_only:q+=" AND status='verifie'"
    q+=' ORDER BY symbol,year,metric'
    return [dict(r) for r in db.execute(q,args)]

def parse_csv(raw):
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))

def coverage(db,symbols):
    out=[]
    for symbol in symbols:
        rs=records(db,symbol,True)
        years=sorted({r['year'] for r in rs},reverse=True)[:5]
        available={(r['year'],r['metric']) for r in rs}
        required=('CA','RN','DIV_NET') if symbol=='SMBC' else ('PNB','RN','DIV_NET')
        count=sum((y,m) in available for y in years for m in required)
        out.append({'Symbole':symbol,'Exercices vérifiés':', '.join(map(str,years)) or '—','Points vérifiés':count,'Couverture sur 5 ans':f'{count}/15','Données en attente':len(records(db,symbol))-len(rs)})
    return out

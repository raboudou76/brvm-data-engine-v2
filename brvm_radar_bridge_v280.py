"""V2.8.0 — passerelle PostgreSQL -> BRVM Radar, lecture seule.
Ne renvoie que les observations VALIDATED, annuelles, XOF, non ambiguës.
"""
from decimal import Decimal
import pandas as pd

SQL = """
SELECT c.ticker, c.company_name, f.fact_id, f.fiscal_year,
       f.fiscal_period, f.period_basis, f.standardized_account,
       f.amount, f.currency, f.quality_status, f.source_page,
       f.scale_basis, f.currency_basis, f.evidence, r.report_url
FROM public.financial_facts AS f
JOIN public.companies AS c ON c.company_id = f.company_id
LEFT JOIN public.financial_reports AS r ON r.report_id = f.report_id
WHERE f.quality_status = 'VALIDATED'
  AND f.fiscal_year IS NOT NULL AND f.currency = 'XOF'
ORDER BY c.ticker, f.fiscal_year, f.standardized_account, f.fact_id
"""

BANK_ACCOUNTS = {'PNB', 'NET_INCOME', 'OPERATING_INCOME', 'EQUITY'}
INDUSTRIAL_ACCOUNTS = {'REVENUE', 'CA', 'NET_INCOME', 'OPERATING_INCOME', 'EQUITY'}

def read_validated(database_url):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, connect_timeout=12, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute(SQL)
            rows = cur.fetchall()
    return pd.DataFrame(rows)

def prepare_export(frame):
    columns = ['ticker','company_name','fiscal_year','standardized_account','amount_xof',
               'precision','quality_status','source_page','report_url','fact_id','sector_type']
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(columns=['ticker','fiscal_year','standardized_account','reason'])
    df = frame.copy()
    df['fiscal_year'] = pd.to_numeric(df['fiscal_year'],errors='coerce')
    df['amount_xof'] = pd.to_numeric(df['amount'],errors='coerce')
    # Annuel seulement. fiscal_period YEAR/FY/ANNUAL, ou période non précisée
    # uniquement si period_basis indique une période annuelle.
    def annual(row):
        p = str(row.get('fiscal_period') or '').strip().upper()
        b = str(row.get('period_basis') or '').strip().upper()
        return p in {'FY','ANNUAL','YEAR','A','12M','EXERCICE','ANNUEL'} or b in {'ANNUAL','FY','YEAR','FULL_YEAR','EXERCICE','ANNUEL'}
    df['annual_ok'] = df.apply(annual,axis=1)
    df['sector_type'] = df['ticker'].map({'BOAB':'BANK','SMBC':'INDUSTRIAL'}).fillna('UNCLASSIFIED')
    df['account_ok'] = df.apply(lambda r: r['standardized_account'] in (BANK_ACCOUNTS if r['sector_type']=='BANK' else INDUSTRIAL_ACCOUNTS if r['sector_type']=='INDUSTRIAL' else set()),axis=1)
    df['precision'] = df.apply(lambda r: 'ROUNDED_MILLION' if r['ticker']=='SMBC' and r['fiscal_year'] in (2024,2025) else ('EXACT_OR_UNSPECIFIED' if r['ticker']=='BOAB' else 'UNSPECIFIED'),axis=1)
    # La classification SMBC est documentée pour le pilote 2024/2025; aucune exactitude n'est déduite pour BOAB.
    reasons=[]
    for _,r in df.iterrows():
        why=[]
        if not r['annual_ok']: why.append('PERIOD_NOT_CONFIRMED_ANNUAL')
        if not r['account_ok']: why.append('ACCOUNT_OR_SECTOR_NOT_MAPPED')
        if pd.isna(r['amount_xof']) or pd.isna(r['fiscal_year']): why.append('MISSING_VALUE_OR_YEAR')
        if why: reasons.append({'ticker':r['ticker'],'fiscal_year':r['fiscal_year'],'standardized_account':r['standardized_account'],'reason':';'.join(why)})
    valid=df[df['annual_ok'] & df['account_ok'] & df['amount_xof'].notna() & df['fiscal_year'].notna()].copy()
    dup=valid.duplicated(['ticker','fiscal_year','standardized_account'],keep=False)
    for _,r in valid[dup].iterrows():
        reasons.append({'ticker':r['ticker'],'fiscal_year':r['fiscal_year'],'standardized_account':r['standardized_account'],'reason':'DUPLICATE_ACCOUNT_YEAR'})
    valid=valid[~dup].copy()
    valid['fiscal_year']=valid['fiscal_year'].astype(int)
    valid['quality_status']='VALIDATED'
    for col in columns:
        if col not in valid: valid[col]=None
    return valid[columns].sort_values(['ticker','fiscal_year','standardized_account']),pd.DataFrame(reasons,columns=['ticker','fiscal_year','standardized_account','reason'])

def compute_indicators(valid):
    if valid.empty:return pd.DataFrame(columns=['ticker','fiscal_year','metric','value','unit','precision_note'])
    result=[]
    for ticker,g in valid.groupby('ticker'):
        byyear={int(y):dict(zip(v['standardized_account'],v['amount_xof'])) for y,v in g.groupby('fiscal_year')}
        typ=g['sector_type'].iloc[0]
        top='PNB' if typ=='BANK' else ('REVENUE' if any('REVENUE' in a for a in byyear.values()) else 'CA')
        for year,vals in sorted(byyear.items()):
            if top in vals and year-1 in byyear and top in byyear[year-1] and byyear[year-1][top]!=0:
                result.append((ticker,year,top+'_GROWTH_PCT',(vals[top]/byyear[year-1][top]-1)*100,'%', 'Based on published precision'))
            if 'NET_INCOME' in vals and top in vals and vals[top]!=0:
                result.append((ticker,year,'NET_MARGIN_PCT',vals['NET_INCOME']/vals[top]*100,'%','Based on published precision'))
    return pd.DataFrame(result,columns=['ticker','fiscal_year','metric','value','unit','precision_note'])

"""BRVM issuer identity registry. No financial values are inferred from aliases."""
import re
import unicodedata

def norm(value):
    value=unicodedata.normalize('NFKD',str(value or '').casefold())
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',''.join(c for c in value if not unicodedata.combining(c))).split())

# Explicit issuer aliases, not fuzzy ticker guesses. Extend as issuer identities are verified.
ISSUERS={
 'SMBC':{'slugs':['smb'],'aliases':['SMB','SMB CI','SOCIETE MULTINATIONALE DE BITUMES']},
 'SGBC':{'slugs':['sgci'],'aliases':['SGCI','SGBCI','SOCIETE GENERALE CI','SOCIETE GENERALE COTE D IVOIRE']},
 'BOAB':{'slugs':['bank-of-africa-bn','boa-benin','bank-of-africa-benin'],'aliases':['BOA BENIN','BANK OF AFRICA BN','BANK OF AFRICA BENIN']},
 'CBIBF':{'slugs':['coris-bank-international','coris-bank-international-bf'],'aliases':['CORIS BANK INT. BF','CORIS BANK INTERNATIONAL','CORIS BANK INTERNATIONAL BURKINA FASO','CORIS BANK INTERNATIONAL BF']},
 'BOABF':{'slugs':['bank-of-africa-bf','boa-burkina-faso'],'aliases':['BOA BURKINA FASO','BANK OF AFRICA BF']},
 'BOAC':{'slugs':['bank-of-africa-ci','boa-cote-d-ivoire'],'aliases':['BOA COTE D IVOIRE','BANK OF AFRICA CI']},
 'BOAM':{'slugs':['bank-of-africa-ml','boa-mali'],'aliases':['BOA MALI','BANK OF AFRICA ML']},
 'BOAN':{'slugs':['bank-of-africa-ng','boa-niger'],'aliases':['BOA NIGER','BANK OF AFRICA NG']},
 'BOAS':{'slugs':['bank-of-africa-sn','boa-senegal'],'aliases':['BOA SENEGAL','BANK OF AFRICA SN']},
}

def identities(symbol,name=''):
    symbol=str(symbol or '').strip().upper()
    spec=ISSUERS.get(symbol,{})
    aliases=list(dict.fromkeys([symbol,name]+spec.get('aliases',[])))
    return {'symbol':symbol,'aliases':[a for a in aliases if a], 'slugs':spec.get('slugs',[])}

def dividend_match(records,symbol,name):
    if records is None or records.empty or 'Société' not in records:return None
    allowed={norm(x) for x in identities(symbol,name)['aliases']}
    matches=[x for x in records['Société'].dropna().astype(str).unique() if norm(x) in allowed]
    return matches[0] if len(matches)==1 else None

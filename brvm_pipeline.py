"""BRVM Data Engine V2.5.0: incremental official-document ingestion.

No financial extraction, approval, or valuation writes in this module.
"""
import hashlib
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

MAX_PDF_BYTES = 30 * 1024 * 1024
DEFAULT_DB = 'sqlite:///brvm_publications_v250.sqlite3'
USER_AGENT = 'BRVM-Data-Engine/2.5.0 (research; public BRVM reports)'


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def engine_for(database_url=None):
    url = database_url or os.getenv('BRVM_PIPELINE_DATABASE_URL') or DEFAULT_DB
    parsed = make_url(url)
    if parsed.get_backend_name() not in ('sqlite', 'postgresql'):
        raise ValueError('Only SQLite and PostgreSQL databases are supported')
    engine = create_engine(url, pool_pre_ping=True, future=True)
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE IF NOT EXISTS pipeline_publications (
            id VARCHAR(64) PRIMARY KEY, symbol VARCHAR(20) NOT NULL,
            title TEXT NOT NULL, fiscal_year INTEGER, period VARCHAR(20),
            source_url TEXT NOT NULL, source_page TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            status VARCHAR(32) NOT NULL, sha256 VARCHAR(64),
            stored_path TEXT, byte_size BIGINT, error TEXT
        )'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS pipeline_runs (
            id VARCHAR(64) PRIMARY KEY, started_at TEXT NOT NULL,
            finished_at TEXT, symbol VARCHAR(20), discovered INTEGER DEFAULT 0,
            new_items INTEGER DEFAULT 0, downloaded INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
            diagnostic TEXT
        )'''))
    return engine


def pdf_url_allowed(url):
    parsed = urlparse(url)
    return (parsed.scheme == 'https' and parsed.hostname in ('brvm.org', 'www.brvm.org')
            and parsed.username is None and parsed.password is None
            and re.search(r'\.pdf(?:$|\?)', url, re.I) is not None)


def download_official_pdf(url, folder, session=None):
    """Stream PDF with a hard size cap and atomic, content-addressed storage."""
    if not pdf_url_allowed(url):
        raise ValueError('Not an official HTTPS BRVM PDF URL')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    client = session or requests.Session()
    response = client.get(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/pdf'},
                          timeout=(8, 35), stream=True, allow_redirects=False)
    try:
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise ValueError('Redirect rejected for safety')
        declared = response.headers.get('Content-Length')
        if declared and int(declared) > MAX_PDF_BYTES:
            raise ValueError('PDF too large')
        digest = hashlib.sha256()
        size = 0
        first = b''
        with tempfile.NamedTemporaryFile(dir=folder, suffix='.part', delete=False) as tmp:
            temporary = Path(tmp.name)
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    if len(first) < 5:
                        first = (first + chunk)[:5]
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise ValueError('PDF too large')
                    digest.update(chunk)
                    tmp.write(chunk)
                if first != b'%PDF-':
                    raise ValueError('Response is not a PDF')
                if size < 20:
                    raise ValueError('Truncated PDF')
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        hexdigest = digest.hexdigest()
        destination = folder / (hexdigest + '.pdf')
        if destination.exists():
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)
        return {'sha256': hexdigest, 'path': str(destination), 'bytes': size}
    finally:
        response.close()


def _id(symbol, url):
    return hashlib.sha256((symbol.upper() + '\n' + url).encode()).hexdigest()


def ingest(symbol, pages=3, max_downloads=12, database_url=None, archive_dir=None,
           discover=None, fetch=None, sleep_seconds=0.4):
    """Incrementally archive official PDFs. `discover`/`fetch` allow offline tests."""
    if discover is None:
        from brvm_catalog import catalog
        discover = catalog
    symbol = symbol.strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{2,15}', symbol):
        raise ValueError('Invalid issuer symbol')
    pages = max(1, min(int(pages), 8))
    max_downloads = max(0, min(int(max_downloads), 100))
    archive = archive_dir or os.getenv('BRVM_ARCHIVE_DIR', 'brvm_pdf_archive')
    engine = engine_for(database_url)
    run_id = hashlib.sha256((symbol + utcnow() + str(time.time_ns())).encode()).hexdigest()
    report = {'run_id': run_id, 'symbol': symbol, 'discovered': 0, 'new_items': 0,
              'downloaded': 0, 'skipped': 0, 'failed': 0, 'errors': []}
    with engine.begin() as c:
        c.execute(text('INSERT INTO pipeline_runs (id, started_at, symbol) VALUES (:id,:at,:symbol)'),
                  {'id': run_id, 'at': utcnow(), 'symbol': symbol})
    try:
        docs, diagnostic = discover(symbol, pages)
        report['catalog_diagnostic'] = diagnostic
        unique = {d['URL']: d for d in docs if d.get('PDF') and pdf_url_allowed(d.get('URL', ''))}
        report['discovered'] = len(unique)
        for url, d in unique.items():
            identifier = _id(symbol, url)
            with engine.begin() as c:
                existing = c.execute(text('SELECT status,stored_path FROM pipeline_publications WHERE id=:id'), {'id': identifier}).mappings().first()
                if existing:
                    c.execute(text('UPDATE pipeline_publications SET last_seen=:now WHERE id=:id'), {'id':identifier,'now':utcnow()})
                else:
                    c.execute(text('''INSERT INTO pipeline_publications
                        (id,symbol,title,fiscal_year,period,source_url,source_page,first_seen,last_seen,status)
                        VALUES (:id,:symbol,:title,:year,:period,:url,:page,:now,:now,'discovered')'''),
                        {'id':identifier,'symbol':symbol,'title':d.get('Titre','')[:500],
                         'year':d.get('Année'),'period':d.get('Période'), 'url':url,
                         'page':d.get('Page source'), 'now':utcnow()})
                    report['new_items'] += 1
            if existing and existing['status']=='downloaded' and existing['stored_path'] and Path(existing['stored_path']).is_file():
                report['skipped'] += 1
                continue
            if report['downloaded'] + report['failed'] >= max_downloads:
                report['skipped'] += 1
                continue
            try:
                info = (fetch or download_official_pdf)(url, archive)
                with engine.begin() as c:
                    c.execute(text('''UPDATE pipeline_publications SET status='downloaded',sha256=:sha,
                        stored_path=:path,byte_size=:size,error=NULL WHERE id=:id'''),
                        {'sha':info['sha256'],'path':info['path'],'size':info['bytes'],'id':identifier})
                report['downloaded'] += 1
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'[:350]
                with engine.begin() as c:
                    c.execute(text("UPDATE pipeline_publications SET status='failed',error=:err WHERE id=:id"),
                              {'err':error,'id':identifier})
                report['failed'] += 1
                report['errors'].append(f'{d.get("Titre",url)[:60]}: {error}')
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    except Exception as exc:
        report['errors'].append(f'Catalogue: {type(exc).__name__}: {exc}')
        report['failed'] += 1
    finally:
        with engine.begin() as c:
            c.execute(text('''UPDATE pipeline_runs SET finished_at=:end,discovered=:discovered,
                new_items=:new_items,downloaded=:downloaded,skipped=:skipped,
                failed=:failed,diagnostic=:diag WHERE id=:id'''),
                {'end':utcnow(), 'id':run_id, 'discovered':report['discovered'],
                 'new_items':report['new_items'],'downloaded':report['downloaded'],
                 'skipped':report['skipped'],'failed':report['failed'],
                 'diag':' | '.join(report['errors'])[:2000]})
    return report


def history(symbol=None, database_url=None, limit=250):
    engine = engine_for(database_url)
    with engine.connect() as c:
        if symbol:
            rows = c.execute(text('''SELECT symbol,title,fiscal_year,period,status,source_url,
                sha256,byte_size,first_seen,last_seen,error FROM pipeline_publications
                WHERE symbol=:symbol ORDER BY first_seen DESC LIMIT :limit'''),
                {'symbol':symbol,'limit':limit})
        else:
            rows = c.execute(text('''SELECT symbol,title,fiscal_year,period,status,source_url,
                sha256,byte_size,first_seen,last_seen,error FROM pipeline_publications
                ORDER BY first_seen DESC LIMIT :limit'''),{'limit':limit})
        return [dict(r) for r in rows.mappings()]


def runs(database_url=None, limit=30):
    engine = engine_for(database_url)
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text('SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT :limit'), {'limit':limit}).mappings()]

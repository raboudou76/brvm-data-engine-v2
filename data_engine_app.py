"""Launch: streamlit run data_engine_app.py"""
import os
import pandas as pd
import streamlit as st
from data_engine import connect,records,stage,approve,parse_csv,coverage
from structured_financials import extract_structured

st.set_page_config(page_title='BRVM Data Engine V2.3',layout='wide')
st.title('BRVM RADAR — Data Engine V2.3')
st.caption('Pilote indépendant : stockage, provenance et validation humaine des données financières')
try: db=connect()
except Exception as exc: st.error(f'Base indisponible : {exc}');st.stop()

symbols=['SMBC','SGBC','BOAB','CBIBF']
for r in db.execute('SELECT DISTINCT symbol FROM financials'):
    if r['symbol'] not in symbols:symbols.append(r['symbol'])
selected=st.selectbox('Société',symbols)
tabs=st.tabs(['Historique vérifié','Synchronisation BRVM','Catalogue documentaire','Importer les rapports','Validation','Contrôle qualité'])
with tabs[0]:
    rs=records(db,selected,True)
    if not rs: st.info('Aucune donnée encore vérifiée pour cette société.')
    else:
        df=pd.DataFrame(rs)
        for metric,title in [('CA','Chiffre d’affaires'),('PNB','Produit net bancaire'),('RN','Résultat net'),('DIV_NET','Dividende net par action')]:
            x=df[df.metric==metric].sort_values('year')
            if not x.empty:
                st.subheader(title)
                st.bar_chart(x.set_index('year')['value'])
                st.dataframe(x[['year','value','unit','source_url','source_page']],hide_index=True,use_container_width=True)
        annual=df[df.metric.isin(['CA','PNB','RN'])].pivot(index='year',columns='metric',values='value').sort_index()
        base='PNB' if 'PNB' in annual else 'CA'
        if base in annual:
            growth=annual[base].pct_change(fill_method=None)*100
            growth=growth.where(pd.Series(annual.index,index=annual.index).diff()==1)
            st.subheader(f'Croissance {base} (%)')
            st.dataframe(growth.dropna().rename('Croissance %').reset_index(),hide_index=True)
with tabs[1]:
    st.subheader('Synchroniser les publications officielles BRVM')
    st.caption('Les données sont téléchargées et placées en attente de validation. Aucune valeur n’est automatiquement certifiée.')
    max_reports=st.slider('Nombre maximum de rapports PDF à analyser',1,12,6)
    financial_pages=st.slider('Pages de publications financières à explorer',1,8,5)
    dividend_pages=st.slider('Pages d’annonces de dividendes à explorer',1,30,12)
    if st.button('Synchroniser les données BRVM',type='primary'):
        from brvm_live_sync import synchronize
        with st.spinner('Recherche et analyse des publications BRVM en cours…'):
            try:
                result=synchronize(db,selected,max_reports,financial_pages,dividend_pages)
                st.session_state[f'sync-{selected}']=result
            except Exception as exc:st.error(f'Synchronisation impossible : {type(exc).__name__}: {exc}')
    result=st.session_state.get(f'sync-{selected}')
    if result:
        cols=st.columns(4)
        for c,label,key in zip(cols,['Rapports repérés','PDF lus','Valeurs financières extraites','Valeurs mises en attente'],['reports_found','pdf_read','financial_rows','staged']):c.metric(label,result[key])
        st.caption(f"Dividendes extraits : {result['dividend_rows']} · PDF en échec : {result['pdf_failed']}")
        if result['report_details']:st.dataframe(pd.DataFrame(result['report_details']),hide_index=True,use_container_width=True)
        cd=result.get('catalog_diagnostic') or {}
        if cd:
            st.caption(f"Catalogue : {cd.get('documents_found',0)} documents, {cd.get('pdf_links',0)} liens PDF")
            if cd.get('pages'):st.dataframe(pd.DataFrame(cd['pages']),hide_index=True,use_container_width=True)
        for error in result['errors']:st.warning(error)
        st.info('Consulte l’onglet Validation pour vérifier les valeurs, puis Historique vérifié pour afficher les histogrammes.')
with tabs[2]:
    st.subheader('Catalogue des publications BRVM')
    st.caption('Recherche indépendante de l’extraction : rapports annuels et T1/S1/T3, sans certification automatique.')
    cat_pages=st.slider('Pages du catalogue par société',1,8,3)
    if st.button('Rechercher les publications officielles',type='primary'):
        from brvm_catalog import catalog
        with st.spinner('Exploration des pages officielles BRVM…'):
            try:st.session_state[f'catalog-{selected}']=catalog(selected,cat_pages)
            except Exception as exc:st.error(f'Catalogue : {type(exc).__name__}: {exc}')
    data=st.session_state.get(f'catalog-{selected}')
    if data:
        docs,diag=data
        st.metric('Documents identifiés',len(docs))
        if docs:
            st.dataframe(pd.DataFrame(docs),hide_index=True,use_container_width=True)
            st.download_button('Exporter le catalogue CSV',pd.DataFrame(docs).to_csv(index=False).encode('utf-8-sig'),f'catalogue_{selected}.csv','text/csv')
        if diag['pages']:st.dataframe(pd.DataFrame(diag['pages']),hide_index=True,use_container_width=True)
        for error in diag['errors']:st.warning(error)
        if not docs:st.warning('Aucune publication détectée : consulter les URL et statuts ci-dessus.')
with tabs[3]:
    st.write('Import CSV de valeurs vérifiées dans les publications, ou pré-extraction d’un PDF natif. Toute valeur importée reste en attente de validation.')
    st.download_button('Télécharger le modèle CSV',open(os.path.join(os.path.dirname(__file__),'modele_import.csv'),'rb').read(),'modele_import.csv')
    csvfile=st.file_uploader('Importer un CSV',type='csv',key='csv')
    if csvfile and st.button('Mettre les valeurs en attente de contrôle'):
        try:
            n,errors=stage(db,parse_csv(csvfile.getvalue()))
            st.success(f'{n} valeurs importées, non encore validées')
            for error in errors:st.warning(error)
        except Exception as exc:st.error(str(exc))
    st.divider()
    pdf=st.file_uploader('PDF financier officiel',type='pdf')
    source=st.text_input('URL HTTPS de la publication officielle (obligatoire pour enregistrer)')
    if pdf and st.button('Pré-extraire le PDF'):
        try:
            _,raw,_=extract_structured(pdf.getvalue())
            rows=[]
            mapping={'chiffre_affaires':'CA','pnb':'PNB','resultat_net':'RN'}
            for _,r in raw.iterrows():
                for ycol,vcol in [('Exercice N','2025 ou N'),('Exercice N-1','2024 ou N-1')]:
                    rows.append(dict(symbol=selected,year=int(r[ycol]),metric=mapping[r['Indicateur']],value=float(r[vcol]),unit='millions FCFA',source_url=source,source_page=str(r['Page']),source_title=pdf.name))
            st.session_state['pdf_rows']=rows
            st.dataframe(pd.DataFrame(rows),hide_index=True)
            st.info('Extraction automatique : montants non encore vérifiés sur le document original.')
        except Exception as exc:st.error(f'Extraction impossible : {exc}')
    if st.session_state.get('pdf_rows') and st.button('Enregistrer les valeurs extraites en attente'):
        n,errors=stage(db,st.session_state['pdf_rows'])
        st.success(f'{n} lignes en attente de validation')
        for err in errors:st.warning(err)
with tabs[4]:
    pending=[r for r in records(db,selected) if r['status']=='a_verifier']
    if not pending:st.info('Aucune valeur en attente pour ce titre.')
    for r in pending:
        with st.expander(f"{r['year']} · {r['metric']} · {r['value']:,.2f} {r['unit']}"):
            st.write('Source :',r['source_url'],'— Page :',r['source_page'])
            st.warning('Comparer avec le PDF officiel avant de valider.')
            if st.button('J’ai vérifié cette valeur dans la source',key=f"ok-{r['symbol']}-{r['year']}-{r['metric']}"):
                approve(db,r['symbol'],r['year'],r['metric']);st.rerun()
with tabs[5]:
    st.dataframe(pd.DataFrame(coverage(db,symbols)),hide_index=True,use_container_width=True)
    st.caption('La couverture mesure uniquement les valeurs validées. Une valeur absente n’est jamais assimilée à zéro.')
    st.download_button('Exporter les données vérifiées',pd.DataFrame(records(db,verified_only=True)).to_csv(index=False).encode('utf-8-sig'),'brvm_financials_verified.csv','text/csv')
st.caption('Attention : sur Streamlit Community Cloud, le fichier SQLite local peut disparaître au redéploiement. Utiliser un volume persistant ou PostgreSQL avant production.')

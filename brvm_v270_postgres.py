
import os
import streamlit as st


def get_database_url():
    """Lit l'adresse PostgreSQL sans exposer le secret."""
    try:
        url = st.secrets.get("DATABASE_URL")
    except Exception:
        url = None

    return url or os.getenv("DATABASE_URL")


def tester_connexion():
    """Teste la connexion et compte les tables publiques."""
    import psycopg

    url = get_database_url()

    if not url:
        return False, "DATABASE_URL est introuvable."

    try:
        with psycopg.connect(
            url,
            connect_timeout=10,
            sslmode="require"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                """)
                nombre = cur.fetchone()[0]

        return True, f"Connexion réussie. {nombre} tables trouvées."

    except Exception:
        return False, (
            "Connexion PostgreSQL impossible. "
            "Vérifier DATABASE_URL, le réseau et les droits d'accès. "
            "Consulter les journaux privés pour le diagnostic."
        )

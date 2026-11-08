
import os
import socket
import streamlit as st
import psycopg

from urllib.parse import urlsplit, unquote


def get_database_url():
    try:
        return st.secrets.get("DATABASE_URL") or os.getenv("DATABASE_URL")
    except Exception:
        return os.getenv("DATABASE_URL")


def tester_connexion():
    url = get_database_url()

    if not url:
        return False, "Configuration : DATABASE_URL introuvable."

    try:
        parsed = urlsplit(url)

        if parsed.scheme not in ("postgresql", "postgres"):
            return False, "Configuration : protocole PostgreSQL incorrect."

        if not parsed.hostname or not parsed.username:
            return False, "Configuration : adresse PostgreSQL incomplète."

        if not parsed.password or "[YOUR-PASSWORD]" in url:
            return False, "Configuration : mot de passe absent."

        if parsed.port != 6543:
            return False, "Configuration : port différent de 6543."

    except (ValueError, TypeError):
        return False, "Configuration : adresse PostgreSQL mal formée."

    try:
        # Connexion sans affichage des identifiants
        with psycopg.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=parsed.path.lstrip("/") or "postgres",
            user=unquote(parsed.username),
            password=unquote(parsed.password),
            sslmode="require",
            connect_timeout=10
        ) as conn:

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                """)
                nombre = cur.fetchone()[0]

        return True, f"Connexion réussie : {nombre} tables trouvées."

    except psycopg.OperationalError as exc:
        # Classification sans exposer le message brut
        msg = str(exc).lower()

        if "password authentication failed" in msg:
            return False, "Authentification : mot de passe ou utilisateur incorrect."

        if "tenant or user not found" in msg:
            return False, "Supabase : identifiant du projet ou utilisateur pooler incorrect."

        if "name or service not known" in msg or "nodename nor servname" in msg:
            return False, "Réseau : nom du serveur PostgreSQL introuvable."

        if "timeout" in msg or "timed out" in msg:
            return False, "Réseau : délai de connexion dépassé."

        if "connection refused" in msg:
            return False, "Réseau : connexion refusée par le serveur."

        if "certificate" in msg or "ssl" in msg:
            return False, "Sécurité : erreur de connexion SSL."

        return False, "Connexion PostgreSQL : erreur non classifiée."

    except Exception:
        return False, "Erreur technique : connexion non établie."

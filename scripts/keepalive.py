"""Mantiene activa la base de Postgres (plan gratuito de Supabase) haciendo una consulta mínima.

Lo ejecuta GitHub Actions (.github/workflows/keepalive.yml) un par de veces por semana.
Necesita la variable DATABASE_URL. Solo hace SELECT 1: no lee ni modifica datos.
Sale con error si no puede conectarse, para que GitHub avise.
"""
import os
import sys

url = os.environ.get("DATABASE_URL", "").strip()
if not url:
    sys.exit("Falta la variable DATABASE_URL.")

import psycopg  # noqa: E402

try:
    with psycopg.connect(url, prepare_threshold=None, connect_timeout=30) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
except Exception as e:  # no imprimir la URL ni detalles de credenciales
    sys.exit(f"No se pudo consultar la base: {type(e).__name__}")
print("OK: la base respondió.")

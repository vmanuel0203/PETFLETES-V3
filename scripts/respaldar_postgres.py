"""Respaldo de la base de Postgres a archivos CSV (solo lectura: no modifica nada).

Uso (Windows):
    set DATABASE_URL=postgresql://...
    python scripts/respaldar_postgres.py                  crea respaldos/AAAAMMDD-HHMMSS/
    python scripts/respaldar_postgres.py --conservar 10   además deja solo los 10 respaldos más recientes

AVISO: el respaldo contiene datos sensibles (correos, teléfonos, hashes de contraseñas).
Guárdalo fuera de GitHub (la carpeta respaldos/ ya está en .gitignore) y en un lugar seguro.
"""
import argparse
import csv
import json
import os
import re
import shutil
import sys
from datetime import datetime

# Orden de dependencias (las tablas referenciadas primero), igual que el esquema de db.py.
TABLAS = ["usuarios", "tiendas", "productos", "repartidores", "pedidos",
          "publicaciones", "ofertas", "mensajes", "calificaciones"]
_PATRON_CARPETA = re.compile(r"^\d{8}-\d{6}$")


def exportar(conn, tablas, carpeta):
    """Escribe una tabla por archivo CSV y un meta.json con los conteos. `conn` solo necesita .execute(sql)."""
    os.makedirs(carpeta, exist_ok=True)
    conteos = {}
    for tabla in tablas:
        cur = conn.execute(f"SELECT * FROM {tabla} ORDER BY id")
        columnas = [c[0] for c in cur.description]
        filas = cur.fetchall()
        ruta = os.path.join(carpeta, f"{tabla}.csv")
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(columnas)
            for fila in filas:
                w.writerow(["" if v is None else v for v in fila])
        with open(ruta, newline="", encoding="utf-8") as f:   # verificación: se relee el archivo escrito
            n_archivo = sum(1 for _ in csv.reader(f)) - 1
        if n_archivo != len(filas):
            raise RuntimeError(f"Respaldo incompleto en {tabla}: {n_archivo} de {len(filas)} filas.")
        conteos[tabla] = len(filas)
    with open(os.path.join(carpeta, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"creado": datetime.now().isoformat(timespec="seconds"), "filas": conteos}, f, indent=2)
    return conteos


def conservar_ultimos(destino, n):
    """Borra los respaldos más antiguos, dejando n. Solo toca carpetas con nombre AAAAMMDD-HHMMSS."""
    carpetas = sorted(d for d in os.listdir(destino)
                      if _PATRON_CARPETA.match(d) and os.path.isdir(os.path.join(destino, d)))
    borradas = carpetas[:-n] if n > 0 else []
    for d in borradas:
        shutil.rmtree(os.path.join(destino, d))
    return borradas


def main():
    ap = argparse.ArgumentParser(description="Respaldo de la base de Postgres a CSV (solo lectura).")
    ap.add_argument("--destino", default="respaldos", help="carpeta donde se guardan los respaldos")
    ap.add_argument("--conservar", type=int, default=0, help="si se indica, deja solo los N respaldos más recientes")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("Falta la variable DATABASE_URL.")
    import psycopg
    carpeta = os.path.join(args.destino, datetime.now().strftime("%Y%m%d-%H%M%S"))
    conn = psycopg.connect(url, prepare_threshold=None, connect_timeout=30)
    try:
        conn.read_only = True   # garantiza que el respaldo no puede modificar la base
        conteos = exportar(conn, TABLAS, carpeta)
    finally:
        conn.close()
    print(f"Respaldo creado en: {carpeta}")
    for t, n in conteos.items():
        print(f"  {t}: {n} filas")
    print("Recuerda: contiene datos sensibles; no lo subas a GitHub.")
    if args.conservar > 0:
        borradas = conservar_ultimos(args.destino, args.conservar)
        if borradas:
            print(f"Respaldos antiguos eliminados: {len(borradas)}")


if __name__ == "__main__":
    main()

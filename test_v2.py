import glob, hashlib, io, os, secrets, sqlite3, sys, tempfile, contextlib, importlib
import db, tarifas

# Modo servidor (PENDIENTE DE PRUEBA): corre las mismas pruebas contra un Postgres de PRUEBAS.
#   PETFLETES_TEST_DATABASE_URL=postgresql://...   proyecto de pruebas, NUNCA el real
#   PETFLETES_TEST_BORRAR_TABLAS=1                 confirma que se pueden BORRAR las tablas de PetFletes en ese proyecto
URL_PRUEBAS = os.environ.get("PETFLETES_TEST_DATABASE_URL", "").strip()
MODO_PG = bool(URL_PRUEBAS)
if MODO_PG and os.environ.get("PETFLETES_TEST_BORRAR_TABLAS") != "1":
    sys.exit("Modo Postgres: estas pruebas BORRAN las tablas de PetFletes del proyecto indicado. "
             "Usa un proyecto de PRUEBAS y confirma con PETFLETES_TEST_BORRAR_TABLAS=1.")
_TABLAS_BORRAR = ["calificaciones", "mensajes", "ofertas", "publicaciones", "pedidos",
                  "repartidores", "productos", "tiendas", "usuarios"]


def nuevo(nombre, demo=False):
    db.DEMO = demo
    if MODO_PG:
        db.DATABASE_URL, db.PG_LISTO = URL_PRUEBAS, True
        with db.conexion() as c:
            for t in _TABLAS_BORRAR:
                c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        return nombre
    d = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(d, nombre)
    return db.DB_PATH

def esperar_error(fn, *a, **k):
    try: fn(*a, **k)
    except ValueError as e: return str(e)
    raise AssertionError("no lanzó ValueError")

# ---- tarifas
assert tarifas.calcular_tarifa_minima(6, 12) == 4150
assert tarifas.minimo_efectivo(4150, 2800) == 2800 and tarifas.minimo_efectivo(2000, 9000) == 2000

# ---- 1. migración de una base v1 (sin columnas nuevas) con respaldo y sin perder datos
if not MODO_PG:
    ruta = nuevo("v1.db")
    c = sqlite3.connect(ruta)
    c.executescript("""
    CREATE TABLE usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, email TEXT NOT NULL UNIQUE, telefono TEXT, password_hash TEXT NOT NULL, salt TEXT NOT NULL, rol TEXT NOT NULL DEFAULT 'usuario', activo INTEGER NOT NULL DEFAULT 1, creado TEXT NOT NULL);
    CREATE TABLE tiendas (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, direccion TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, descripcion TEXT, activa INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE productos (id INTEGER PRIMARY KEY AUTOINCREMENT, tienda_id INTEGER NOT NULL, nombre TEXT NOT NULL, precio INTEGER NOT NULL, peso_kg REAL NOT NULL, categoria TEXT, activo INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE repartidores (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, sinpe TEXT NOT NULL, vehiculo TEXT NOT NULL, usuario_id INTEGER);
    CREATE TABLE pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, tienda_id INTEGER NOT NULL, items_json TEXT NOT NULL, peso_total REAL NOT NULL, subtotal INTEGER NOT NULL, distancia_km REAL NOT NULL, tarifa_minima INTEGER NOT NULL, oferta INTEGER NOT NULL, oferta_inicial INTEGER, origen_nombre TEXT, origen_lat REAL, origen_lon REAL, destino_nombre TEXT NOT NULL, destino_senas TEXT NOT NULL, destino_lat REAL NOT NULL, destino_lon REAL NOT NULL, estado TEXT NOT NULL DEFAULT 'Buscando repartidor', repartidor_id INTEGER, creado TEXT NOT NULL, publicado_en TEXT, aceptado_en TEXT, entregado_en TEXT);
    """)
    salt = "abc"
    h = hashlib.pbkdf2_hmac("sha256", b"admin123", salt.encode(), 100_000).hex()
    c.execute("INSERT INTO usuarios (nombre,email,telefono,password_hash,salt,rol,creado) VALUES ('Administrador','admin@petfletes.cr','',?,?,'admin','2026-01-01 00:00:00')", (h, salt))
    c.execute("INSERT INTO usuarios (nombre,email,telefono,password_hash,salt,rol,creado) VALUES ('Cli','cli@x.cr','1',?,?,'usuario','2026-01-01 00:00:00')", (h, salt))
    c.execute("INSERT INTO tiendas (nombre,direccion,lat,lon) VALUES ('Vieja','dir',9.9,-84.1)")
    c.execute("INSERT INTO pedidos (usuario_id,tienda_id,items_json,peso_total,subtotal,distancia_km,tarifa_minima,oferta,destino_nombre,destino_senas,destino_lat,destino_lon,creado) VALUES (2,1,'[]',1,5000,3,2000,2500,'d','s',9.9,-84.0,datetime('now','localtime'))")
    c.commit(); c.close()
    antes = os.path.getsize(ruta)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        db.init_db()
    baks = glob.glob(ruta + ".bak-*")
    assert len(baks) == 1 and os.path.getsize(baks[0]) == antes, "respaldo faltante o distinto"
    c = sqlite3.connect(ruta)
    assert c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 2, "no debe crear cuentas demo ni otro admin"
    assert c.execute("SELECT estado, oferta FROM pedidos").fetchone() == ("Buscando repartidor", 2500)
    assert "sinpe" in [r[1] for r in c.execute("PRAGMA table_info(tiendas)")]
    c.close()
    assert db.admin_usa_clave_antigua() is True
    db.init_db()  # segunda vez: no hay migración pendiente -> no más respaldos
    assert len(glob.glob(ruta + ".bak-*")) == 1
    # el pedido legado sigue visible para repartidores (flujo anterior intacto)
    assert [p["id"] for p in db.listar_pedidos_disponibles()] == [1]
    # cambiar contraseña
    assert "actual" in esperar_error(db.cambiar_password, 1, "mala", "nueva123")
    db.cambiar_password(1, "admin123", "nueva123")
    assert db.admin_usa_clave_antigua() is False and db.autenticar("admin@petfletes.cr", "nueva123")
    print("1 OK migración")
else:
    print("1 OMITIDA en modo Postgres (migra un archivo SQLite v1)")

# ---- 2. base nueva sin demo: admin aleatorio impreso, sin datos de prueba
nuevo("nueva.db")
with contextlib.redirect_stdout(io.StringIO()) as out:
    db.init_db()
assert "Contraseña inicial" in out.getvalue()
pw = out.getvalue().split("solo esta vez): ")[1].split()[0]
assert db.autenticar("admin@petfletes.cr", pw)["rol"] == "admin"
assert db.autenticar("demo@petfletes.cr", "demo123") is None
assert db.listar_tiendas() == [] and db.estadisticas_admin()["usuarios"] == 0
assert db.simular_ofertas(1) == 0
db.init_db(); assert not glob.glob(db.DB_PATH + ".bak-*")
print("2 OK primer arranque")

# ---- 3. modo demo: flujo completo
nuevo("demo.db", demo=True)
db.ADMIN_PASSWORD = "AdminTest1"
with contextlib.redirect_stdout(io.StringIO()):
    db.init_db()
assert db.autenticar("admin@petfletes.cr", "AdminTest1")["rol"] == "admin"
tu = db.autenticar("tienda@petfletes.cr", "demo123")
assert tu["rol"] == "tienda" and tu["tienda_id"] == 1
cli = db.autenticar("demo@petfletes.cr", "demo123"); rep = db.autenticar("repartidor@petfletes.cr", "demo123")
items = [{"producto_id": 1, "nombre": "Arena", "cantidad": 1, "precio": 3000, "peso_kg": 1.0}]
args = dict(items=items, peso_total=1.0, subtotal=3000, distancia_km=8.0, tarifa_minima=3900,
            origen_nombre="T", origen_lat=9.9, origen_lon=-84.1, destino_nombre="D", destino_senas="s",
            destino_lat=9.95, destino_lon=-84.05)
# tope: envío <= productos; mínimo efectivo = min(3900, 3000) = 3000
assert "superar" in esperar_error(db.crear_pedido, cli["id"], 1, oferta=3100, **args)
assert "mínima" in esperar_error(db.crear_pedido, cli["id"], 1, oferta=2900, **args)
assert "subtotal" in esperar_error(db.crear_pedido, cli["id"], 1, **{**args, "subtotal": 100}, oferta=3000)
pid = db.crear_pedido(cli["id"], 1, oferta=3000, **args)
p = db.obtener_pedido(pid, cli["id"])
assert p["estado"] == "Esperando tienda" and p["tarifa_minima"] == 3000 and p["tienda_sinpe"] == "8800-1001"
assert db.listar_pedidos_disponibles() == [], "no debe publicarse antes de la confirmación de la tienda"
assert esperar_error(db.enviar_oferta, pid, rep["repartidor_id"], 1000)
assert db.aceptar_viaje(pid, rep["repartidor_id"]) is False
# otra tienda no puede confirmar
tu2 = db.autenticar("tienda2@petfletes.cr", "demo123")
assert db.confirmar_pedido_tienda(pid, tu2["id"]) is False
assert db.admin_confirmar_pedido(pid) is False  # la tienda tiene cuenta propia
assert db.confirmar_pedido_tienda(pid, tu["id"]) is True
assert db.confirmar_pedido_tienda(pid, tu["id"]) is False
p = db.obtener_pedido(pid, cli["id"])
assert p["estado"] == "Buscando repartidor" and p["pago_confirmado_en"] and p["segundos_restantes"] > 290
assert [x["id"] for x in db.listar_pedidos_disponibles()] == [pid]
# repartidor: contraoferta por encima del tope rechazada
assert "superar" in esperar_error(db.enviar_oferta, pid, rep["repartidor_id"], 3500)
db.enviar_oferta(pid, rep["repartidor_id"], 2500)
# ajustes del cliente respetan tope y mínimo
db.ajustar_tarifa(pid, cli["id"], 100); assert db.obtener_pedido(pid, cli["id"])["oferta"] == 3000
db.ajustar_tarifa(pid, cli["id"], -100); assert db.obtener_pedido(pid, cli["id"])["oferta"] == 3000
assert db.marcar_listo_tienda(pid, tu["id"]) is True and db.marcar_listo_tienda(pid, tu["id"]) is False
assert db.simular_ofertas(pid) == 3
assert all(o["monto"] <= 3000 for o in db.listar_ofertas(pid))
assert db.aceptar_viaje(pid, rep["repartidor_id"]) is True
p = db.obtener_pedido(pid, cli["id"]); assert p["estado"] == "Aceptado" and p["oferta"] == 3000
assert db.marcar_entregado_repartidor(pid, rep["repartidor_id"])
assert db.obtener_pedido(pid, cli["id"])["estado"] == "Entregado"

# pedido con más margen: ajustes de ₡100 dentro del rango [min, subtotal]
items2 = [{"producto_id": 2, "nombre": "Alimento", "cantidad": 1, "precio": 28900, "peso_kg": 15}]
a2 = {**args, "items": items2, "subtotal": 28900, "tarifa_minima": 3900}
pid2 = db.crear_pedido(cli["id"], 1, oferta=3900, **a2)
db.confirmar_pedido_tienda(pid2, tu["id"])
db.ajustar_tarifa(pid2, cli["id"], 100); assert db.obtener_pedido(pid2, cli["id"])["oferta"] == 4000
db.ajustar_tarifa(pid2, cli["id"], -1000); assert db.obtener_pedido(pid2, cli["id"])["oferta"] == 3900
db.ajustar_tarifa(pid2, cli["id"], 99999); assert db.obtener_pedido(pid2, cli["id"])["oferta"] == 28900
# rechazo de la tienda con nota
pid3 = db.crear_pedido(cli["id"], 1, oferta=3900, **a2)
assert db.rechazar_pedido_tienda(pid3, tu["id"], "Sin pago recibido")
p3 = db.obtener_pedido(pid3, cli["id"]); assert p3["estado"] == "Cancelado" and p3["nota_tienda"] == "Sin pago recibido"
# cancelación del cliente en espera, y del admin
pid4 = db.crear_pedido(cli["id"], 1, oferta=3900, **a2); assert db.cancelar_pedido(pid4, cli["id"])
pid5 = db.crear_pedido(cli["id"], 1, oferta=3900, **a2); assert db.admin_cancelar_pedido(pid5)
pid6 = db.crear_pedido(cli["id"], 1, oferta=3900, **a2)
assert {p["id"] for p in db.listar_pedidos_admin()} == {pid2, pid6}
assert db.estadisticas_admin()["esperando"] == 1 and len(db.listar_pedidos_tienda(tu["id"])) == 6
assert db.listar_pedidos_tienda(tu2["id"]) == []
# tienda sin cuenta: confirma el admin
tid_n = db.crear_tienda("Sin dueño", "dir", 9.9, -84.0, "", "8888-2222", "2222-0000")
pid7 = db.crear_pedido(cli["id"], tid_n, oferta=3900, **a2)
assert db.admin_confirmar_pedido(pid7) is True
# catálogo de la tienda: aislamiento
pr = db.listar_productos(1, solo_activos=False)[0]
db.actualizar_producto_tienda(tu["id"], pr["id"], "Nuevo nombre", 1000, 1.0, "Alimento", True)
assert "no pertenece" in esperar_error(db.actualizar_producto_tienda, tu2["id"], pr["id"], "x", 1000, 1.0, "Alimento", True)
db.crear_producto_tienda(tu["id"], "Otro", 500, 0.5, "Juguetes")
db.actualizar_mi_tienda(tu["id"], "PetZone", "Nueva dir", "desc", "8800-9999", "2288-0000")
assert db.obtener_tienda_de_usuario(tu["id"])["sinpe"] == "8800-9999"
# registro de tienda: queda oculta hasta aprobación
nt = db.crear_usuario("Dueña", "dueña@x.cr", "7000-1111", "secreto1", rol="tienda",
    tienda={"nombre": "Pet Nueva", "direccion": "Av 1", "lat": 9.93, "lon": -84.09, "sinpe": "70001111", "descripcion": ""})
assert nt["tienda_id"] and db.obtener_tienda(nt["tienda_id"])["activa"] == 0
assert "Pet Nueva" not in [t["nombre"] for t in db.listar_tiendas()]
assert esperar_error(db.crear_pedido, cli["id"], nt["tienda_id"], oferta=3900, **a2) == "Esa tienda no está disponible."
assert "SINPE" in esperar_error(db.crear_usuario, "X", "x@y.cr", "1", "secreto1", rol="tienda",
    tienda={"nombre": "a", "direccion": "b", "lat": 1, "lon": 1, "sinpe": "12"})
db.actualizar_tienda(nt["tienda_id"], "Pet Nueva", "Av 1", "", True)
assert "Pet Nueva" in [t["nombre"] for t in db.listar_tiendas()]
assert db.autenticar("dueña@x.cr", "secreto1")["tienda_id"] == nt["tienda_id"]
print("3 OK flujo demo")

# ---- 4. rutas que la prueba 3 no cubre: búsqueda, elegir oferta, chats, calificaciones, comunidad y listados admin
# (en modo Postgres comprueba ILIKE, AVG/ROUND, GROUP BY y las excepciones de integridad contra el servidor)
nuevo("demo5.db", demo=True)
db.ADMIN_PASSWORD = "AdminTest1"
with contextlib.redirect_stdout(io.StringIO()):
    db.init_db()
adm = db.autenticar("admin@petfletes.cr", "AdminTest1")
tu = db.autenticar("tienda@petfletes.cr", "demo123")
cli = db.autenticar("demo@petfletes.cr", "demo123"); rep = db.autenticar("repartidor@petfletes.cr", "demo123")
cat = db.listar_productos(1)[0]["categoria"]
db.crear_producto_tienda(tu["id"], "Arena Premium Zeta", 3000, 1.0, cat)
assert "Arena Premium Zeta" in [x["nombre"] for x in db.buscar_productos("aReNa PREMIUM zeta")], "búsqueda sin distinguir mayúsculas"
assert db.buscar_productos("xyz-no-existe") == []
assert "superar" not in esperar_error(db.crear_usuario, "Otro", "demo@petfletes.cr", "1", "secreto1")  # correo duplicado
assert esperar_error(db.crear_usuario, "Otro", "demo@petfletes.cr", "1", "secreto1") == "Ese correo ya está registrado."

items5 = [{"producto_id": 1, "nombre": "Arena", "cantidad": 1, "precio": 3000, "peso_kg": 1.0}]
pid = db.crear_pedido(cli["id"], 1, items=items5, peso_total=1.0, subtotal=3000, distancia_km=8.0, tarifa_minima=3900,
                      origen_nombre="T", origen_lat=9.9, origen_lon=-84.1, destino_nombre="D", destino_senas="s",
                      destino_lat=9.95, destino_lon=-84.05, oferta=3000)
assert db.confirmar_pedido_tienda(pid, tu["id"]) is True
db.enviar_oferta(pid, rep["repartidor_id"], 2500)
ofs = db.listar_ofertas(pid)
assert len(ofs) == 1 and ofs[0]["monto"] == 2500 and ofs[0]["promedio"] is None and ofs[0]["n_cal"] == 0
assert db.elegir_oferta(pid, ofs[0]["id"], cli["id"]) is True and db.elegir_oferta(pid, ofs[0]["id"], cli["id"]) is False
p = db.obtener_pedido(pid, cli["id"])
assert p["estado"] == "Aceptado" and p["oferta"] == 2500 and p["repartidor_id"] == rep["repartidor_id"]

# chat del pedido (solo cliente y repartidor, solo con el viaje activo)
db.enviar_mensaje_pedido(pid, cli["id"], "Hola, ¿ya vienes?"); db.enviar_mensaje_pedido(pid, rep["id"], "En camino")
assert [m["texto"] for m in db.listar_mensajes_pedido(pid, cli["id"])] == ["Hola, ¿ya vienes?", "En camino"]
assert [m["autor"] for m in db.listar_mensajes_pedido(pid, rep["id"])][0] == cli["nombre"]
assert db.listar_mensajes_pedido(pid, tu["id"]) == []
assert esperar_error(db.enviar_mensaje_pedido, pid, tu["id"], "intruso") == "No puedes escribir en este chat."

# chat de soporte y lista de hilos (consulta con GROUP BY)
db.enviar_mensaje_soporte(cli["id"], cli["id"], "Necesito ayuda"); db.enviar_mensaje_soporte(cli["id"], adm["id"], "Claro, dime")
db.enviar_mensaje_soporte(rep["id"], rep["id"], "Tengo una duda")
hilos = {h["id"]: h for h in db.listar_hilos_soporte()}
assert set(hilos) == {cli["id"], rep["id"]}
assert hilos[cli["id"]]["total"] == 2 and hilos[cli["id"]]["pendiente"] is False and hilos[cli["id"]]["ultimo_texto"] == "Claro, dime"
assert hilos[rep["id"]]["total"] == 1 and hilos[rep["id"]]["pendiente"] is True
assert [m["texto"] for m in db.listar_mensajes_soporte(cli["id"], adm["id"])] == ["Necesito ayuda", "Claro, dime"]
assert db.listar_mensajes_soporte(cli["id"], rep["id"]) == []

# entrega y calificaciones (duplicada = excepción de integridad)
assert "entregado" in esperar_error(db.guardar_calificacion, pid, "usuario", cli["id"], 5, "antes de tiempo")
assert db.marcar_entregado_repartidor(pid, rep["repartidor_id"]) is True
assert esperar_error(db.enviar_mensaje_pedido, pid, cli["id"], "tarde") == "El chat solo está activo mientras dura el viaje."
db.guardar_calificacion(pid, "usuario", cli["id"], 5, "Excelente servicio")
db.guardar_calificacion(pid, "repartidor", rep["repartidor_id"], 4, "")
assert esperar_error(db.guardar_calificacion, pid, "usuario", cli["id"], 5, "otra vez") == "Ya enviaste tu calificación para este pedido."
pr, pu = db.promedio_repartidor(rep["repartidor_id"]), db.promedio_usuario(cli["id"])
assert pr == (5.0, 1) and pu == (4.0, 1) and isinstance(pr[0], float) and isinstance(pu[0], float)
fila_r = [r for r in db.listar_repartidores_admin() if r["id"] == rep["repartidor_id"]][0]
fila_u = [u for u in db.listar_usuarios_admin() if u["id"] == cli["id"]][0]
assert fila_r["calificacion"] == 5.0 and fila_r["n_cal"] == 1 and fila_r["entregas"] == 1 and isinstance(fila_r["calificacion"], float)
assert fila_u["calificacion"] == 4.0 and fila_u["n_cal"] == 1 and fila_u["pedidos"] == 1 and isinstance(fila_u["calificacion"], float)

# comunidad y estadísticas públicas (LIMIT, promedios)
db.crear_publicacion(tu["id"], "tip", "Título de prueba", "Contenido de prueba")
assert [x["titulo"] for x in db.listar_publicaciones("tip")][0] == "Título de prueba"
act = db.actividad_comunidad(50)
assert any(a["clase"] == "resena" and a["texto"] == "Excelente servicio" for a in act)
assert any(a["clase"] == "publicacion" and a["titulo"] == "Título de prueba" for a in act)
assert len(db.actividad_comunidad(1)) == 1
est = db.estadisticas_publicas()
assert est["n_cal"] >= 1 and isinstance(est["promedio"], float) and est["entregados"] >= 1
print("4 OK rutas adicionales (búsqueda, ofertas, chats, calificaciones, comunidad, admin)")

# ---- 5. adaptadores de Postgres (sin servidor): traducción de SQL, filas, esquema y bloqueo de seguridad
# Estas pruebas NO tocan ningún servidor: usan objetos simulados. No sustituyen la prueba real (ver PENDIENTE_PRUEBA_SERVIDOR.txt).
assert db._traducir_sql("SELECT * FROM t WHERE a = ? AND b LIKE ?") == "SELECT * FROM t WHERE a = %s AND b LIKE %s"
assert db._traducir_sql("SELECT '100%' WHERE x = ?") == "SELECT '100%%' WHERE x = %s"
f = db._Fila(("id", "nombre"), (7, "Ana"))
assert f[0] == 7 and f["nombre"] == "Ana" and f[-1] == "Ana" and tuple(f) == (7, "Ana")
assert dict(f) == {"id": 7, "nombre": "Ana"} and f.keys() == ["id", "nombre"]
try:
    f["no_existe"]
    raise AssertionError("debía fallar")
except KeyError:
    pass


class _Col:
    def __init__(self, name): self.name = name


class _CurFalso:
    description = [_Col("id"), _Col("email")]


assert db._fabrica_filas(_CurFalso())((1, "a@b.cr"))["email"] == "a@b.cr"
from decimal import Decimal
_fila_dec = db._fabrica_filas(_CurFalso())((Decimal("4.5"), "x"))
assert isinstance(_fila_dec["id"], float) and _fila_dec["id"] == 4.5   # AVG/ROUND de Postgres -> float


class _CurIns:
    rowcount = 1
    def fetchone(self): return (42,)


class _ConnFalsa:
    def __init__(self): self.visto = []
    def execute(self, sql, params=()):
        self.visto.append((sql, params))
        return _CurIns() if "RETURNING" in sql else "cursor"


cf = _ConnFalsa()
pg = db._ConexionPG(cf)
assert pg.execute("UPDATE t SET a = ? WHERE id = ?", (1, 2)) == "cursor"
cur = pg.execute("INSERT INTO usuarios (nombre) VALUES (?)", ("Ana",))
assert cur.lastrowid == 42 and cur.rowcount == 1                      # lastrowid vía RETURNING id
assert cf.visto[-1] == ("INSERT INTO usuarios (nombre) VALUES (%s) RETURNING id", ("Ana",))
pg.execute("INSERT INTO t (a) VALUES (?) RETURNING id", (1,))           # no duplica RETURNING
assert cf.visto[-1][0].count("RETURNING") == 1
cf.visto.clear(); pg.executescript("CREATE TABLE a (id INTEGER);\nCREATE TABLE b (id INTEGER);\n")
assert [v[0].strip() for v in cf.visto] == ["CREATE TABLE a (id INTEGER)", "CREATE TABLE b (id INTEGER)"]

# Esquema y consultas según el motor (se simula Postgres cambiando DATABASE_URL temporalmente)
url_antes, listo_antes, prueba_antes = db.DATABASE_URL, db.PG_LISTO, db.PG_PRUEBA
try:
    db.DATABASE_URL = ""
    assert db._sql_dialecto(db.SCHEMA) == db.SCHEMA and db._op_like() == "LIKE"
    assert db._errores_integridad() == (sqlite3.IntegrityError,)
    db.DATABASE_URL = "postgresql://usuario:clave@host:5432/db"
    esquema_pg = db._sql_dialecto(db.SCHEMA)
    assert "AUTOINCREMENT" not in esquema_pg and "REAL" not in esquema_pg
    assert esquema_pg.count("GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY") == 9
    assert esquema_pg.count("DOUBLE PRECISION") == db.SCHEMA.count("REAL") > 0
    assert db._sql_dialecto("REAL") == "DOUBLE PRECISION" and db._op_like() == "ILIKE"
    assert all("REAL" not in db._sql_dialecto(d) or "REALTIME" in d for _, _, d in db._MIGRACIONES)

    # Candado: con DATABASE_URL y sin PG_LISTO ni opt-in de prueba, la app se niega a arrancar
    db.PG_LISTO, db.PG_PRUEBA = False, False
    try:
        with db.conexion():
            raise AssertionError("debía bloquearse")
    except RuntimeError as e:
        assert "PETFLETES_PG_PRUEBA" in str(e)
finally:
    db.DATABASE_URL, db.PG_LISTO, db.PG_PRUEBA = url_antes, listo_antes, prueba_antes
assert db.usa_postgres() == MODO_PG
if not MODO_PG:
    nuevo("guardia.db")
    with db.conexion() as c:  # SQLite sigue funcionando igual
        assert c.execute("SELECT 1").fetchone()[0] == 1
print("5 OK adaptadores Postgres (simulados, sin servidor)")
# ---- 6. scripts de mantenimiento: respaldo a CSV (probado con SQLite) y archivos del keepalive
import importlib.util, csv as _csv, json as _json, subprocess
_raiz = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("respaldar_postgres", os.path.join(_raiz, "scripts", "respaldar_postgres.py"))
resp = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(resp)
assert resp.TABLAS == db._TABLAS, "el orden/lista de tablas del respaldo debe coincidir con el esquema"
if not MODO_PG:
    nuevo("resp.db", demo=True)
    with contextlib.redirect_stdout(io.StringIO()):
        db.init_db()
    conn = sqlite3.connect(db.DB_PATH)
    dest = os.path.join(tempfile.mkdtemp(), "respaldos")
    carpeta = os.path.join(dest, "20260101-000000")
    conteos = resp.exportar(conn, resp.TABLAS, carpeta)
    assert conteos["usuarios"] >= 5 and conteos["tiendas"] >= 2 and conteos["pedidos"] == 0
    with open(os.path.join(carpeta, "usuarios.csv"), newline="", encoding="utf-8") as f:
        filas = list(_csv.reader(f))
    assert filas[0][:3] == ["id", "nombre", "email"] and len(filas) - 1 == conteos["usuarios"]
    meta = _json.load(open(os.path.join(carpeta, "meta.json"), encoding="utf-8"))
    assert meta["filas"] == conteos and set(os.listdir(carpeta)) == {f"{t}.csv" for t in resp.TABLAS} | {"meta.json"}
    assert conn.total_changes == 0, "el respaldo no debe modificar la base"
    conn.close()
    # conservar los N más recientes: solo toca carpetas AAAAMMDD-HHMMSS
    for n in ("20260102-000000", "20260103-000000", "20260104-000000"):
        os.makedirs(os.path.join(dest, n))
    os.makedirs(os.path.join(dest, "otra_carpeta"))
    assert resp.conservar_ultimos(dest, 2) == ["20260101-000000", "20260102-000000"]
    assert sorted(os.listdir(dest)) == ["20260103-000000", "20260104-000000", "otra_carpeta"]
# keepalive: sin DATABASE_URL falla con mensaje claro; el workflow existe y usa el secreto, no una URL escrita
_env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
r = subprocess.run([sys.executable, os.path.join(_raiz, "scripts", "keepalive.py")], env=_env, capture_output=True, text=True)
assert r.returncode != 0 and "DATABASE_URL" in (r.stderr + r.stdout)
flujo = open(os.path.join(_raiz, ".github", "workflows", "keepalive.yml"), encoding="utf-8").read()
assert "secrets.DATABASE_URL" in flujo and "cron:" in flujo and "postgresql://" not in flujo
print("6 OK respaldo y keepalive (lógica probada sin servidor)")
db.cerrar_pool()  # modo Postgres: cierra las conexiones antes de salir

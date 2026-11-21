#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSINT-Auto :: Investigador OSINT automatizado
Menú interactivo que orquesta Maigret, Sherlock, Holehe, Ignorant y Blackbird
y deja los reportes (HTML + PDF + CSV) en output/<fecha>_<objetivo>/.
"""
import getpass
import os
import subprocess
import sys
from pathlib import Path

# Permite ejecutar el script desde cualquier carpeta (debe ir ANTES de importar `modules`)
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Carga opcional de variables de entorno desde `.env` (ej. HIBP_API_KEY, BLACKBIRD_PATH)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# Importaciones unificadas (se eliminaron las duplicadas que estaban antes de sys.path)
from modules import utils, reports  # noqa: E402
from modules.username_search import run_username_search  # noqa: E402
from modules.email_search import (  # noqa: E402
    run_email_search,
    run_email_lookup,
    run_email_osint,
    generate_emails,
)
from modules.phone_search import run_phone_search  # noqa: E402
from modules.blackbird import run_blackbird  # noqa: E402
from modules.breach_check import (  # noqa: E402
    check_email_breaches,
    check_password_k_anonymity,
    format_breach_report,
    breaches_csv_rows,
)

BANNER = r"""
   ____  _____ _____ _   _ _______           _         _
  / __ \/ ____|_   _| \ | |__   __|     /\  | |       | |
 | |  | | (___   | | |  \| |  | |______ /  \ | |_ _   _| |_ ___
 | |  | |\___ \  | | | . ` |  | |______/ /\ \| __| | | | __/ _ \
 | |__| |____) |_| |_| |\  |  | |     / ____ \ |_| |_| | || (_) |
  \____/|_____/|_____|_| \_|  |_|    /_/    \_\__|\__,_|\__\___/
                                   Investigador OSINT  v1.0
"""

COUNTRIES = ["co", "mx", "es", "us", "ar", "cl", "pe", "ve", "ec", "uy"]
TOPS = [100, 200, 300, 500]


def _confirmar_uso() -> bool:
    """Pide confirmar que hay autorización o interés legítimo antes de investigar a alguien."""
    utils.warn("Úsalo solo con fuentes públicas y sobre personas/cuentas que tengas autorización "
               "o motivo legítimo para investigar (tu propia huella, auditoría, periodismo, seguridad).")
    return utils.ask_yes_no("¿Confirmas que tu uso es legítimo?", default=False)


def _pedir_pais(default="co"):
    """Pide un código de país; avisa si no está en la lista pero lo respeta."""
    utils.info(f"Países sugeridos: {', '.join(COUNTRIES)}")
    c = utils.ask("Código de país", default).lower()
    if c not in COUNTRIES:
        utils.warn(f"'{c}' no está en la lista; se usará igual.")
    return c


def _reportar(out_dir: Path, etiqueta: str, extra: dict = None):
    """
    Consolida resultados de Maigret, Sherlock y Blackbird (deduplicados + nivel de confianza)
    y genera CSV, HTML y PDF en out_dir.
    """
    encontrados = reports.consolidate_profiles(out_dir)
    ins = reports.build_insights(encontrados)
    if encontrados:
        reports.write_csv(out_dir, reports.rows_for_csv(encontrados))
    reports.write_html(out_dir, etiqueta, ins, extra)
    reports.write_pdf(out_dir, etiqueta, {**ins, **(extra or {})})


def opt_username():
    utils.title("Búsqueda por USERNAME")
    username = utils.ask("Username a investigar")
    if not username:
        utils.err("Username vacío.")
        return
    country = _pedir_pais() if utils.ask_yes_no("¿Filtrar por país?", False) else None
    top = utils.ask_int("Cantidad de sitios:", 200, TOPS) if utils.ask_yes_no("¿Limitar a top-sites?", True) else None

    out_dir = utils.make_output_dir(f"user_{username}")
    utils.info(f"Carpeta de salida: {out_dir}")
    run_username_search(username, out_dir, country=country, top=top)
    _reportar(out_dir, f"username:{username}")
    utils.ok(f"Reportes en {out_dir}")


def opt_country():
    utils.title("Búsqueda por PAÍS / ZONA")
    username = utils.ask("Username a investigar")
    if not username:
        return
    country = _pedir_pais()
    top = utils.ask_int("Top-sites:", 300, TOPS)
    out_dir = utils.make_output_dir(f"geo_{country}_{username}")
    run_username_search(username, out_dir, country=country, top=top)
    _reportar(out_dir, f"{username} ({country})")
    utils.ok(f"Reportes en {out_dir}")


# ===========================================================================
# CORREO ELECTRÓNICO
# ===========================================================================
def opt_email_lookup(force_mode: str = None):
    """
    Investiga un correo conocido.
    Si `force_mode` es "1" (solo Holehe) o "2" (Holehe + usernames derivados),
    evita volver a preguntar el modo al usuario.
    """
    utils.title("Búsqueda DIRECTA por CORREO ELECTRÓNICO")
    email = utils.ask("Correo a investigar (ej: juan.perez@gmail.com)")
    if not email or "@" not in email:
        utils.err("Correo inválido.")
        return

    mode = force_mode or utils.ask(
        "Modo: [1] solo Holehe (rápido)  /  [2] Holehe + usernames derivados",
        "1",
    )

    out_dir = utils.make_output_dir(f"mail_{email.replace('@','_at_')}")
    utils.info(f"Carpeta de salida: {out_dir}")

    if mode == "2":
        result = run_email_osint(email, out_dir)
        services = result["services"]
        derived = result["profiles_found"]
        rate_limited = result.get("rate_limited", [])
    else:
        result = run_email_lookup(email, out_dir)
        services = result.get("registered_details") or result.get("registered", [])
        derived = {}
        rate_limited = result.get("rate_limited", [])

    # Resumen legible de validación DNS/MX y Gravatar
    mx = result.get("mx") or {}
    grav = result.get("gravatar") or {}
    grav_info = (
        [
            f"Perfil: {grav.get('profile_url') or 'sí'}",
            f"Nombre visible: {grav.get('display_name') or '—'}",
            f"Username: {grav.get('preferred_username') or '—'}",
            f"Ubicación: {grav.get('location') or '—'}",
            *(grav.get("accounts") or []),
        ]
        if grav.get("found")
        else ["Sin perfil público en Gravatar"]
    )

    # Consulta de exposición en brechas de datos (XposedOrNot + HIBP si hay API key)
    breach_res = check_email_breaches(email, out_dir=out_dir)

    # ---- Reportes ----
    extra = {
        "Correo": email,
        "Validación DNS / MX": f"{mx.get('note', '—')} | MX: {', '.join(mx.get('mx_records') or []) or '—'}",
        "Gravatar público": grav_info,
        "Servicios registrados (Holehe)": services or ["(ninguno confirmado)"],
        "Servicios con rate-limit (no concluyentes)": (
            [f"{len(rate_limited)} servicios bloqueados: {', '.join(rate_limited[:20])}"]
            if rate_limited
            else ["Ninguno"]
        ),
        **format_breach_report(breach_res),
        "Usernames derivados probados": result.get("usernames_tested", []) or ["—"],
        "Perfiles asociados a usernames derivados": derived or {"—": "sin hits"},
    }
    reports.write_html(out_dir, f"mail:{email}", extra)
    reports.write_pdf(out_dir, f"mail:{email}", extra)

    if services:
        reports.write_csv(
            out_dir,
            [{"email": email, "servicio": s, "estado": "registrado"} for s in services],
        )
    if breach_res.get("breaches"):
        reports.write_csv(out_dir, breaches_csv_rows(breach_res), filename="brechas_detectadas.csv")

    utils.title("BÚSQUEDA POR CORREO FINALIZADA")
    utils.ok(f"Reportes en: {out_dir}")


def opt_email_known_menu():
    """
    Submenú dedicado a la investigación por correo:
      [1] Verificar un correo concreto (Holehe)
      [2] Correo + usernames derivados (Holehe + Maigret/Sherlock)
      [3] Generar correos candidatos desde un username (comportamiento anterior)
    """
    utils.title("INVESTIGACIÓN POR CORREO ELECTRÓNICO")
    print("  [1] Verificar un correo conocido (Holehe)")
    print("  [2] Correo conocido + usernames derivados (Holehe + Maigret/Sherlock)")
    print("  [3] Generar correos candidatos a partir de un username (Holehe)")
    print("  [0] Volver")
    sub = utils.ask("Opción", "0")

    # Se reutiliza opt_email_lookup pasando force_mode para evitar duplicar código y repreguntar
    if sub == "1":
        opt_email_lookup(force_mode="1")
    elif sub == "2":
        opt_email_lookup(force_mode="2")
    elif sub == "3":
        opt_emails()
    elif sub == "0":
        return
    else:
        utils.warn("Opción inválida.")
        
def opt_emails():
    utils.title("Búsqueda de CORREOS a partir de USERNAME")
    username = utils.ask("Username base")
    if not username:
        return
    candidatos = generate_emails(username)
    utils.info(f"Se generaron {len(candidatos)} candidatos. Primeros 10:")
    for c in candidatos[:10]:
        print(f"   - {c}")

    limit = utils.ask_int("¿Cuántos verificar con Holehe?", 8, [4, 8, 12, 20])
    out_dir = utils.make_output_dir(f"email_{username}")
    found = run_email_search(username, out_dir, limit=limit)

    if found:
        utils.ok(f"Correos registrados: {len(found)}")
        filas = []
        for email, servicios in found.items():
            print(f"\n  {email}")
            for s in servicios:
                print(f"     + {s}")
            filas.append({"email": email, "servicios": ";".join(servicios)})
        reports.write_csv(out_dir, filas)
    else:
        utils.warn("No se detectaron correos registrados.")

    ins = {"Correos registrados": [f"{e}: {', '.join(s)}" for e, s in found.items()]}
    reports.write_html(out_dir, f"email:{username}", ins)
    reports.write_pdf(out_dir, f"email:{username}", ins)



def opt_phone():
    utils.title("Búsqueda por NÚMERO DE TELÉFONO")
    phone = utils.ask("Número en formato internacional (ej: +573001234567)")
    if not phone:
        return
    out_dir = utils.make_output_dir(f"phone_{phone.replace('+', '')}")
    res = run_phone_search(phone, out_dir)
    an = res.get("analysis") or {}

    # Estructuramos el reporte con datos técnicos (`phonenumbers`) + servicios parseados de Ignorant
    secciones = {
        "Análisis técnico del número": [
            f"Formato E.164: {an.get('e164') or phone}",
            f"Válido: {'Sí' if an.get('valid') else 'No / Revisar'}",
            f"País / Región: {an.get('location') or an.get('region_code') or 'desconocido'}",
            f"Operador (carrier): {an.get('carrier') or 'no determinado'}",
            f"Tipo de línea: {an.get('line_type') or 'desconocido'}",
            f"Zonas horarias: {', '.join(an.get('timezones') or []) or '—'}",
        ],
        "Servicios registrados (Ignorant)": res.get("registered") or ["(ninguno confirmado)"],
        "Servicios no registrados": res.get("not_used") or ["—"],
        "Servicios con rate-limit (no concluyentes)": res.get("rate_limited") or ["Ninguno"],
    }
    reports.write_html(out_dir, f"phone:{res.get('phone', phone)}", secciones)
    reports.write_pdf(out_dir, f"phone:{res.get('phone', phone)}", secciones)

    # Guardamos también un CSV estructurado del teléfono
    filas_csv = [
        {"telefono": res.get("phone", phone), "servicio": s, "estado": "registrado"}
        for s in res.get("registered", [])
    ] + [
        {"telefono": res.get("phone", phone), "servicio": s, "estado": "rate_limit"}
        for s in res.get("rate_limited", [])
    ]
    if filas_csv:
        reports.write_csv(out_dir, filas_csv, filename="telefono_servicios.csv")
    utils.ok(f"Reportes en: {out_dir}")


def opt_full():
    """Pipeline completo: username -> correos -> Blackbird -> teléfono -> reportes."""
    utils.title("BÚSQUEDA COMPLETA")
    username = utils.ask("Username del objetivo")
    if not username:
        return
    country = _pedir_pais() if utils.ask_yes_no("¿Filtrar Maigret por país?", False) else None
    phone = utils.ask("Teléfono (opcional, Enter para omitir)", "")
    
    known_email = utils.ask(
    "Correo conocido del objetivo (opcional, Enter para omitir)", ""
    
)
    
    out_dir = utils.make_output_dir(f"full_{username}")
    utils.title(f"Pipeline completo -> {out_dir}")

    utils.info("Paso 1/4 :: username")
    run_username_search(username, out_dir, country=country, top=300)
    
    

    utils.info("Paso 2/4 :: correos")
    correos = run_email_search(username, out_dir, limit=8)

    # Corregido: indentación del bloque y uso del diccionario correcto `correos` (antes `found_emails`).
    # Se ejecuta antes de Blackbird para priorizar el correo conocido si el usuario lo proporcionó.
    if known_email and "@" in known_email:
        utils.info("Paso 2.5 :: correo conocido (Holehe + derivados)")
        known_res = run_email_osint(known_email, out_dir)
        if known_res.get("services"):
            correos[known_email] = known_res["services"]

    utils.info("Paso 3/4 :: Blackbird")
    # Prioriza el correo conocido si existe; si no, usa el primer correo confirmado por Holehe
    correo_blackbird = known_email if (known_email and "@" in known_email) else next(iter(correos), None)
    run_blackbird(username, out_dir, email=correo_blackbird)

    phone_res = None
    if phone:
        utils.info("Paso 4/5 :: teléfono")
        phone_res = run_phone_search(phone, out_dir)

    # Paso 5: verificación de brechas sobre el correo conocido (o el primero detectado por Holehe)
    breach_res = None
    correo_brechas = known_email if (known_email and "@" in known_email) else next(iter(correos), None)
    if correo_brechas:
        utils.info(f"Paso 5/5 :: brechas de datos ({correo_brechas})")
        breach_res = check_email_breaches(correo_brechas, out_dir=out_dir)

    extra = {
        "Correos registrados": [f"{e}: {', '.join(s)}" for e, s in correos.items()] or ["(ninguno)"],
    }
    if breach_res:
        extra.update(format_breach_report(breach_res))
    if phone_res:
        an = phone_res.get("analysis") or {}
        extra["Teléfono"] = [
            f"Número: {phone_res.get('phone', phone)}",
            f"País/Operador/Tipo: {an.get('location') or '?'} / {an.get('carrier') or '?'} / {an.get('line_type') or '?'}",
            f"Ignorant registrados: {', '.join(phone_res.get('registered') or []) or 'ninguno'}",
            f"Ignorant rate-limit: {', '.join(phone_res.get('rate_limited') or []) or 'ninguno'}",
        ]
    else:
        extra["Teléfono"] = "no proporcionado"

    _reportar(out_dir, f"FULL:{username}", extra)
    if correos:
        # Guardamos los correos en un CSV separado para no sobrescribir resumen_consolidado.csv de perfiles
        reports.write_csv(
            out_dir,
            [{"email": e, "servicios": ";".join(s)} for e, s in correos.items()],
            filename="correos_encontrados.csv",
        )
    if breach_res and breach_res.get("breaches"):
        reports.write_csv(out_dir, breaches_csv_rows(breach_res), filename="brechas_detectadas.csv")
    utils.title("BÚSQUEDA COMPLETA FINALIZADA")
    utils.ok(f"Revisa los archivos en: {out_dir}")


def opt_breach_menu():
    """
    Submenú de verificación defensiva de brechas de datos:
      [1] Verificar si un correo aparece en brechas conocidas (XposedOrNot + HIBP opcional)
      [2] Auditar una contraseña PROPIA con k-Anonymity (HIBP Pwned Passwords)
    """
    utils.title("DETECCIÓN DE BRECHAS DE DATOS")
    print("  [1] Verificar correo en brechas conocidas (XposedOrNot + HIBP si hay API key)")
    print("  [2] Auditar una contraseña PROPIA (k-Anonymity, no se envía ni guarda la clave)")
    print("  [0] Volver")
    sub = utils.ask("Opción", "0")

    if sub == "1":
        if not _confirmar_uso():
            return
        email = utils.ask("Correo a verificar (ej: usuario@dominio.com)")
        if not email or "@" not in email:
            utils.err("Correo inválido.")
            return
        out_dir = utils.make_output_dir(f"breach_{email.replace('@', '_at_')}")
        res = check_email_breaches(email, out_dir=out_dir)
        secciones = format_breach_report(res)
        reports.write_html(out_dir, f"breach:{email}", secciones)
        reports.write_pdf(out_dir, f"breach:{email}", secciones)
        if res.get("breaches"):
            reports.write_csv(out_dir, breaches_csv_rows(res), filename="brechas_detectadas.csv")
        utils.ok(f"Reportes de brechas en: {out_dir}")
    elif sub == "2":
        utils.info("Protocolo k-Anonymity: solo se envían los primeros 5 caracteres del hash SHA-1.")
        utils.info("La contraseña NO se muestra en pantalla ni se guarda en ningún archivo.")
        try:
            pwd = getpass.getpass("?> Ingresa tu contraseña propia a auditar: ")
        except (EOFError, KeyboardInterrupt):
            return
        if not pwd:
            utils.warn("Contraseña vacía.")
            return
        res = check_password_k_anonymity(pwd)
        if res.get("error"):
            utils.err(res["error"])
        elif res["compromised"]:
            utils.warn(
                f"COMPROMETIDA: esta contraseña apareció {res['count']:,} veces en filtraciones públicas. "
                "Cámbiala de inmediato donde la estés usando y evita reutilizarla."
            )
        else:
            utils.ok("NO encontrada en la base pública de Pwned Passwords.")
    elif sub == "0":
        return
    else:
        utils.warn("Opción inválida.")


def opt_history():
    utils.title("REPORTES ANTERIORES")
    lista = utils.list_reports()
    if not lista:
        utils.warn("No hay reportes en output/.")
        return
    for i, d in enumerate(lista, 1):
        archivos = [f.name for f in d.iterdir() if f.is_file()]
        print(f"  {i:2d}. {d.name}\n       archivos: {', '.join(archivos) or '(vacía)'}")
    sel = utils.ask("Número para abrir la carpeta (Enter para volver)", "")
    if sel.isdigit() and 0 < int(sel) <= len(lista):
        ruta = lista[int(sel) - 1]
        utils.info(f"Ruta: {ruta}")
        try:
            if sys.platform.startswith("win"):
                os.startfile(ruta)  # noqa: S606 - solo existe en Windows
            elif sys.platform == "darwin":
                subprocess.run(["open", str(ruta)], check=False)
            else:
                subprocess.run(["xdg-open", str(ruta)], check=False)
        except Exception as e:  # noqa: BLE001
            utils.warn(f"No se pudo abrir automáticamente: {e}")


MENU = {
    "1": ("Buscar por nombre de usuario", opt_username),
    "2": ("Buscar filtrando por país / zona", opt_country),
    "3": ("Buscar por correo electrónico",  opt_email_known_menu),
    "4": ("Buscar correos a partir de username", opt_emails),
    "5": ("Buscar por número de teléfono", opt_phone),
    "6": ("Búsqueda COMPLETA (todo automático)", opt_full),
    "7": ("Detectar brechas de datos (correo / contraseña propia)", opt_breach_menu),
    "8": ("Ver reportes anteriores", opt_history),
    "0": ("Salir", None),
}
# Las opciones que investigan a una persona piden confirmación (la opción 7 confirma dentro del submenú de correo)
REQUIEREN_CONFIRMACION = {"1", "2", "3", "4", "5", "6"}


def main():
    print(BANNER)
    utils.check_tools()
    while True:
        print()
        utils.title("MENÚ PRINCIPAL")
        for key, (label, _) in MENU.items():
            print(f"  [{key}] {label}")
        choice = utils.ask("Elige una opción", "0")

        if choice == "0":
            utils.info("¡Hasta luego!")
            break
        accion = MENU.get(choice, (None, None))[1]
        if accion is None:
            utils.warn("Opción inválida.")
            continue
        if choice in REQUIEREN_CONFIRMACION and not _confirmar_uso():
            continue
        try:
            accion()
        except KeyboardInterrupt:
            utils.warn("\nInterrumpido por el usuario.")
        except Exception as e:  # noqa: BLE001 - el menú no debe morir por un error de una opción
            utils.err(f"Error en la opción {choice}: {e}")


if __name__ == "__main__":
    main()

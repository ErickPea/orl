# -*- coding: utf-8 -*-
"""
Extractor de metadatos forenses/OSINT para imágenes, fotos y videos:
  - Imágenes (JPG, PNG, WebP, TIFF, BMP, GIF, HEIC):
      * EXIF completo (cámara/celular, fecha original, lente, ISO, software) con Pillow.
      * Conversión precisa de coordenadas GPS (DMS -> decimal) + enlaces a OpenStreetMap y Google Maps.
      * Lectura de bloques XMP, texto PNG y comentarios JPEG.
      * Sanitizador defensivo: genera una copia limpia de la imagen sin metadatos EXIF/GPS/XMP.
  - Videos (MP4, MOV, MKV, AVI, WebM, 3GP):
      * Extracción completa con `ffprobe` (si está instalado): dispositivo, fecha, códecs,
        resolución, duración, FPS y coordenadas GPS en formato ISO 6709.
      * Respaldo en Python puro para contenedores MP4/MOV cuando `ffprobe` no está disponible.
  - Soporte opcional de `exiftool` si existe en el sistema.
"""
import hashlib
import json
import mimetypes
import re
import shutil
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import ExifTags, Image, ImageOps
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".3gp", ".wmv", ".flv"}

# Regex para coordenadas GPS estándar ISO 6709 usadas en videos de iPhone/Android (ej. "+04.7110-074.0721+2600.0/")
_ISO6709_RE = re.compile(r"([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?/?")

# Etiquetas XMP útiles dentro de bloques XML embebidos en imágenes
_XMP_PATTERNS = {
    "XMP CreatorTool": re.compile(r"<xmp:CreatorTool>([^<]+)</xmp:CreatorTool>", re.I),
    "XMP CreateDate": re.compile(r"<xmp:CreateDate>([^<]+)</xmp:CreateDate>", re.I),
    "XMP ModifyDate": re.compile(r"<xmp:ModifyDate>([^<]+)</xmp:ModifyDate>", re.I),
    "Photoshop DateCreated": re.compile(r"<photoshop:DateCreated>([^<]+)</photoshop:DateCreated>", re.I),
    "DC Creator": re.compile(r"<dc:creator>.*?<rdf:li>([^<]+)</rdf:li>", re.I | re.S),
    "DC Description": re.compile(r"<dc:description>.*?<rdf:li[^>]*>([^<]+)</rdf:li>", re.I | re.S),
}

# Claves frecuentes de dispositivo / autor / software para la evaluación de privacidad
_DEVICE_KEYS = ("make", "model", "lensmodel", "com.apple.quicktime.make", "com.apple.quicktime.model", "com.android.manufacturer", "com.android.model")
_SOFTWARE_KEYS = ("software", "encoder", "com.apple.quicktime.software", "xmp creatortool", "processingsoftware")
_AUTHOR_KEYS = ("artist", "author", "copyright", "dc creator", "ownername", "cameraownername")
_DATE_KEYS = ("datetimeoriginal", "datetimedigitized", "datetime", "creation_time", "xmp createdate", "com.apple.quicktime.creationdate")


def media_capabilities() -> dict:
    """Indica qué motores de análisis multimedia están disponibles en el sistema."""
    return {
        "pillow": PILLOW_OK,
        "ffprobe": bool(shutil.which("ffprobe")),
        "exiftool": bool(shutil.which("exiftool")),
    }


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{num_bytes} B"


def _compute_hashes(path: Path) -> dict:
    """Calcula SHA-256 y MD5 por bloques (seguro para archivos grandes como videos)."""
    h_sha256 = hashlib.sha256()
    h_md5 = hashlib.md5()  # noqa: S324 - uso exclusivamente informativo/forense
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h_sha256.update(chunk)
            h_md5.update(chunk)
    return {"sha256": h_sha256.hexdigest(), "md5": h_md5.hexdigest()}


def _to_float(val) -> float:
    """Convierte racionales EXIF (IFDRational, tuplas (num, den), int/float) a float."""
    if isinstance(val, tuple) and len(val) == 2:
        return float(val[0]) / float(val[1]) if val[1] else 0.0
    return float(val)


def _gps_links(lat: float, lon: float, altitude: float = None) -> dict:
    lat_r = round(float(lat), 6)
    lon_r = round(float(lon), 6)
    out = {
        "latitude": lat_r,
        "longitude": lon_r,
        "openstreetmap_url": f"https://www.openstreetmap.org/?mlat={lat_r}&mlon={lon_r}#map=16/{lat_r}/{lon_r}",
        "google_maps_url": f"https://www.google.com/maps?q={lat_r},{lon_r}",
    }
    if altitude is not None:
        out["altitude_m"] = round(float(altitude), 1)
    return out


def _parse_exif_gps(gps_ifd: dict) -> dict:
    """Convierte el sub-IFD GPS de EXIF (grados/minutos/segundos) a latitud y longitud decimal."""
    if not gps_ifd:
        return {}
    gps_named = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps_ifd.items()}

    lat_dms = gps_named.get("GPSLatitude")
    lat_ref = str(gps_named.get("GPSLatitudeRef", "N")).upper()
    lon_dms = gps_named.get("GPSLongitude")
    lon_ref = str(gps_named.get("GPSLongitudeRef", "E")).upper()
    if not (lat_dms and lon_dms and len(lat_dms) == 3 and len(lon_dms) == 3):
        return {}

    try:
        lat = _to_float(lat_dms[0]) + _to_float(lat_dms[1]) / 60.0 + _to_float(lat_dms[2]) / 3600.0
        lon = _to_float(lon_dms[0]) + _to_float(lon_dms[1]) / 60.0 + _to_float(lon_dms[2]) / 3600.0
        if lat_ref.startswith("S"):
            lat = -abs(lat)
        if lon_ref.startswith("W"):
            lon = -abs(lon)

        alt = None
        if gps_named.get("GPSAltitude") is not None:
            alt = _to_float(gps_named["GPSAltitude"])
            if str(gps_named.get("GPSAltitudeRef", 0)) in ("1", "b'\\x01'"):
                alt = -abs(alt)
        return _gps_links(lat, lon, alt)
    except Exception:  # noqa: BLE001
        return {}


def parse_iso6709_location(text: str) -> dict:
    """Parsea cadenas GPS ISO 6709 de videos (ej. '+04.7110-074.0721+2600.0/')."""
    if not text:
        return {}
    m = _ISO6709_RE.search(str(text).strip())
    if not m:
        return {}
    try:
        lat = float(m.group(1))
        lon = float(m.group(2))
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return {}
        alt = float(m.group(3)) if m.group(3) else None
        return _gps_links(lat, lon, alt)
    except ValueError:
        return {}


def _clean_value(val) -> str:
    """Normaliza valores EXIF/binarios a texto legible para reportes JSON/HTML."""
    if isinstance(val, bytes):
        try:
            txt = val.decode("utf-8", errors="ignore").strip("\x00 ")
            return txt[:240] if txt else f"<{len(val)} bytes>"
        except Exception:  # noqa: BLE001
            return f"<{len(val)} bytes>"
    if isinstance(val, (list, tuple)):
        return ", ".join(_clean_value(x) for x in val[:8])[:240]
    return str(val).strip("\x00 ")[:240]


def _extract_xmp_from_bytes(raw_bytes: bytes) -> dict:
    """Busca paquetes XML XMP embebidos en los bytes del archivo."""
    out = {}
    start = raw_bytes.find(b"<x:xmpmeta")
    if start == -1:
        return out
    end = raw_bytes.find(b"</x:xmpmeta>", start)
    bloque = raw_bytes[start:(end + 12) if end != -1 else start + 65536]
    texto = bloque.decode("utf-8", errors="ignore")
    for label, pat in _XMP_PATTERNS.items():
        m = pat.search(texto)
        if m and m.group(1).strip():
            out[label] = m.group(1).strip()[:200]
    return out


def _inspect_image(path: Path) -> dict:
    """Extrae información técnica, EXIF, GPS y XMP de una imagen con Pillow."""
    res = {"technical": {}, "metadata": {}, "gps": {}, "engine": ["pillow"]}
    if not PILLOW_OK:
        res["error"] = "Pillow no está instalado."
        return res

    with Image.open(path) as img:
        res["technical"] = {
            "Formato": img.format or path.suffix.lstrip(".").upper(),
            "Resolución": f"{img.width} x {img.height} px",
            "Modo de color": img.mode,
        }
        if img.info.get("dpi"):
            res["technical"]["DPI"] = _clean_value(img.info["dpi"])

        # 1) EXIF principal + sub-IFDs (ExifOffset y GPSInfo)
        exif = img.getexif()
        if exif:
            for tag_id, val in exif.items():
                name = ExifTags.TAGS.get(tag_id, f"Tag_{tag_id}")
                if name in ("GPSInfo", "ExifOffset", "MakerNote"):
                    continue
                limpio = _clean_value(val)
                if limpio:
                    res["metadata"][name] = limpio

            # Sub-IFD Exif (0x8769): DateTimeOriginal, LensModel, ISO, ExposureTime, etc.
            try:
                exif_ifd = exif.get_ifd(0x8769)
                for tag_id, val in (exif_ifd or {}).items():
                    name = ExifTags.TAGS.get(tag_id, f"Exif_{tag_id}")
                    if name == "MakerNote":
                        continue
                    limpio = _clean_value(val)
                    if limpio:
                        res["metadata"][name] = limpio
            except Exception:  # noqa: BLE001
                pass

            # Sub-IFD GPS (0x8825)
            try:
                gps_ifd = exif.get_ifd(0x8825)
                gps = _parse_exif_gps(gps_ifd)
                if gps:
                    res["gps"] = gps
            except Exception:  # noqa: BLE001
                pass

        # 2) Bloques de texto PNG / comentarios JPEG / XMP en img.info
        for k, v in (img.info or {}).items():
            if k in ("exif", "icc_profile", "photoshop", "jfif", "jfif_version", "jfif_unit", "jfif_density", "dpi", "progressive", "compression"):
                continue
            limpio = _clean_value(v)
            if limpio:
                res["metadata"][f"Info:{k}"] = limpio

    # 3) Escaneo ligero de XMP embebido en los primeros 512 KB del archivo
    try:
        raw_head = path.read_bytes()[:524_288]
        for k, v in _extract_xmp_from_bytes(raw_head).items():
            res["metadata"].setdefault(k, v)
    except OSError:
        pass

    return res


def _inspect_video_ffprobe(path: Path) -> dict:
    """Usa `ffprobe` para extraer metadatos completos de contenedor, streams y GPS de un video."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    cmd = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if p.returncode != 0 or not p.stdout.strip():
            return {}
        data = json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {}

    fmt = data.get("format") or {}
    tags = fmt.get("tags") or {}
    streams = data.get("streams") or []

    technical = {
        "Contenedor": fmt.get("format_long_name") or fmt.get("format_name") or path.suffix.lstrip(".").upper(),
    }
    if fmt.get("duration"):
        try:
            secs = float(fmt["duration"])
            mins, rem = divmod(int(secs), 60)
            technical["Duración"] = f"{mins:02d}:{rem:02d} ({secs:.1f} s)"
        except ValueError:
            pass
    if fmt.get("bit_rate"):
        try:
            technical["Bitrate"] = f"{int(fmt['bit_rate']) // 1000} kbps"
        except ValueError:
            pass

    metadata = {}
    gps = {}
    for k, v in tags.items():
        val = _clean_value(v)
        if not val:
            continue
        metadata[k] = val
        if not gps and ("location" in k.lower() or "xyz" in k.lower()):
            gps = parse_iso6709_location(val)

    # Metadatos de pistas de video y audio
    for st in streams:
        ctype = st.get("codec_type")
        if ctype == "video" and "Resolución" not in technical:
            if st.get("width") and st.get("height"):
                technical["Resolución"] = f"{st['width']} x {st['height']} px"
            technical["Códec de video"] = st.get("codec_long_name") or st.get("codec_name") or "—"
            if st.get("avg_frame_rate") and "/" in str(st["avg_frame_rate"]):
                num, den = str(st["avg_frame_rate"]).split("/", 1)
                try:
                    if float(den) > 0:
                        technical["FPS"] = f"{float(num) / float(den):.2f}"
                except ValueError:
                    pass
        elif ctype == "audio" and "Códec de audio" not in technical:
            technical["Códec de audio"] = st.get("codec_long_name") or st.get("codec_name") or "—"
            if st.get("sample_rate"):
                technical["Audio"] = f"{st['sample_rate']} Hz ({st.get('channels', '?')} ch)"

        for tk, tv in (st.get("tags") or {}).items():
            val = _clean_value(tv)
            if val:
                metadata.setdefault(f"stream.{ctype}.{tk}", val)

    return {"technical": technical, "metadata": metadata, "gps": gps, "engine": ["ffprobe"]}


def _inspect_video_mp4_fallback(path: Path) -> dict:
    """
    Respaldo ligero en Python puro para archivos MP4/MOV cuando `ffprobe` no está disponible:
    lee marca del contenedor (`ftyp`), fecha `mvhd` y busca etiquetas GPS ISO 6709.
    """
    technical = {"Contenedor": path.suffix.lstrip(".").upper()}
    metadata = {}
    gps = {}
    try:
        # Leemos cabecera y cola del archivo (en MP4 el átomo `moov` suele estar al inicio o al final)
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(min(size, 524_288))
            tail = b""
            if size > 524_288:
                fh.seek(max(0, size - 524_288))
                tail = fh.read(524_288)
        sample = head + tail

        if len(head) >= 12 and head[4:8] == b"ftyp":
            brand = head[8:12].decode("ascii", errors="ignore").strip()
            if brand:
                technical["Marca MP4 (ftyp)"] = brand

        # Átomo mvhd: timestamp en segundos desde 1904-01-01 (época QuickTime)
        idx = sample.find(b"mvhd")
        if idx != -1 and idx + 20 <= len(sample):
            version = sample[idx + 4]
            if version == 0:
                ctime = struct.unpack(">I", sample[idx + 8:idx + 12])[0]
                if ctime > 2_082_844_800:  # > 1970-01-01
                    dt = datetime.fromtimestamp(ctime - 2_082_844_800, tz=timezone.utc)
                    metadata["creation_time"] = dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        # Átomo ©xyz (ubicación GPS en QuickTime/MP4)
        xyz_idx = sample.find(b"\xa9xyz")
        if xyz_idx != -1:
            snippet = sample[xyz_idx + 4:xyz_idx + 64].decode("latin-1", errors="ignore")
            gps = parse_iso6709_location(snippet)
            if gps:
                metadata["QuickTime:GPS"] = f"{gps['latitude']}, {gps['longitude']}"
    except OSError:
        pass

    return {"technical": technical, "metadata": metadata, "gps": gps, "engine": ["mp4_python_fallback"]}


def _run_exiftool_optional(path: Path) -> dict:
    """Si `exiftool` está instalado, complementa la extracción con etiquetas avanzadas."""
    exe = shutil.which("exiftool")
    if not exe:
        return {}
    try:
        p = subprocess.run([exe, "-j", "-n", str(path)], capture_output=True, text=True,
                           timeout=25, encoding="utf-8", errors="replace")
        if p.returncode != 0 or not p.stdout.strip():
            return {}
        arr = json.loads(p.stdout)
        if not arr or not isinstance(arr[0], dict):
            return {}
        ignorar = {"SourceFile", "Directory", "FileName", "FilePermissions"}
        return {k: _clean_value(v) for k, v in arr[0].items() if k not in ignorar and v not in (None, "")}
    except Exception:  # noqa: BLE001
        return {}


def _build_media_privacy(result: dict) -> dict:
    """Evalúa qué información sensible expone el archivo multimedia."""
    meta = result.get("metadata") or {}
    gps = result.get("gps") or {}
    keys_lower = {k.lower(): v for k, v in meta.items()}

    has_gps = bool(gps.get("latitude") is not None and gps.get("longitude") is not None)
    device = next((v for k, v in keys_lower.items() if any(d in k for d in _DEVICE_KEYS)), None)
    software = next((v for k, v in keys_lower.items() if any(s in k for s in _SOFTWARE_KEYS)), None)
    author = next((v for k, v in keys_lower.items() if any(a in k for a in _AUTHOR_KEYS)), None)
    capture_date = next((v for k, v in keys_lower.items() if any(dt in k for dt in _DATE_KEYS)), None)

    hallazgos = []
    if has_gps:
        hallazgos.append(f"Coordenadas GPS exactas embebidas ({gps['latitude']}, {gps['longitude']}).")
    if device:
        hallazgos.append(f"Dispositivo / cámara identificable: {device}.")
    if capture_date:
        hallazgos.append(f"Fecha y hora original presentes: {capture_date}.")
    if software:
        hallazgos.append(f"Software / encoder detectado: {software}.")
    if author:
        hallazgos.append(f"Autor / propietario en metadatos: {author}.")

    if has_gps:
        nivel = "ALTO"
    elif device or author or capture_date:
        nivel = "MODERADO"
    else:
        nivel = "BAJO"

    if not hallazgos:
        hallazgos.append(
            "No se encontraron metadatos EXIF/GPS sensibles. Es habitual si el archivo pasó por "
            "WhatsApp (foto), Instagram, Facebook, X o TikTok, que eliminan estos datos al subirlo."
        )

    recomendaciones = []
    if has_gps:
        recomendaciones.append("Desactivar el etiquetado de ubicación en la cámara si no deseas compartir dónde fueron tomadas tus fotos/videos.")
    if has_gps or device or author:
        recomendaciones.append("Usar la copia limpia sin metadatos antes de publicar o compartir el archivo públicamente.")
    else:
        recomendaciones.append("El archivo tiene una exposición baja en metadatos; verificar también elementos visuales si es una auditoría de privacidad.")

    return {
        "level": nivel,
        "has_gps": has_gps,
        "device": device,
        "software": software,
        "author": author,
        "capture_date": capture_date,
        "findings": hallazgos,
        "recommendations": recomendaciones,
    }


def strip_image_metadata(src_path: Path, dst_path: Path) -> bool:
    """
    Crea una copia limpia de una imagen eliminando EXIF, GPS, XMP y comentarios,
    preservando la orientación visual correcta (`exif_transpose`).
    """
    if not PILLOW_OK:
        return False
    try:
        with Image.open(src_path) as img:
            corregida = ImageOps.exif_transpose(img)
            fmt = img.format or "PNG"
            # Al copiar solo los píxeles en una imagen nueva se descartan EXIF, XMP e info
            limpia = Image.new(corregida.mode, corregida.size)
            limpia.putdata(list(corregida.getdata()))
            save_kwargs = {}
            if fmt.upper() in ("JPEG", "JPG"):
                if limpia.mode not in ("RGB", "L"):
                    limpia = limpia.convert("RGB")
                save_kwargs["quality"] = 94
                fmt = "JPEG"
            limpia.save(dst_path, format=fmt, **save_kwargs)
        return True
    except Exception:  # noqa: BLE001
        return False


def inspect_media(path: Path, original_name: str = None, generate_clean_copy: bool = True,
                  out_dir: Path = None) -> dict:
    """
    Punto de entrada principal para analizar una imagen o video local.
    Devuelve diccionario estructurado con info de archivo, hashes, técnica, GPS,
    metadatos completos, evaluación de privacidad y (para imágenes) ruta de copia limpia.
    """
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        media_type = "image"
    elif ext in VIDEO_EXTENSIONS:
        media_type = "video"
    else:
        mime, _ = mimetypes.guess_type(str(path))
        if mime and mime.startswith("image/"):
            media_type = "image"
        elif mime and mime.startswith("video/"):
            media_type = "video"
        else:
            media_type = "unknown"

    size = path.stat().st_size
    hashes = _compute_hashes(path)
    mime_type, _ = mimetypes.guess_type(original_name or path.name)

    if media_type == "image":
        extracted = _inspect_image(path)
    elif media_type == "video":
        extracted = _inspect_video_ffprobe(path) or _inspect_video_mp4_fallback(path)
    else:
        extracted = {"technical": {}, "metadata": {}, "gps": {}, "engine": [],
                     "error": "Formato no reconocido como imagen o video compatible."}

    # Enriquecimiento opcional con ExifTool si está instalado
    et_tags = _run_exiftool_optional(path)
    if et_tags:
        extracted.setdefault("engine", []).append("exiftool")
        for k, v in et_tags.items():
            extracted.setdefault("metadata", {}).setdefault(k, v)
        if not extracted.get("gps") and et_tags.get("GPSLatitude") and et_tags.get("GPSLongitude"):
            try:
                extracted["gps"] = _gps_links(float(et_tags["GPSLatitude"]), float(et_tags["GPSLongitude"]))
            except ValueError:
                pass

    result = {
        "filename": original_name or path.name,
        "media_type": media_type,
        "mime_type": mime_type or "application/octet-stream",
        "size_bytes": size,
        "size_human": _human_size(size),
        "hashes": hashes,
        "engines": extracted.get("engine", []),
        "technical": extracted.get("technical", {}),
        "gps": extracted.get("gps", {}),
        "metadata": extracted.get("metadata", {}),
        "metadata_count": len(extracted.get("metadata", {})),
        "error": extracted.get("error"),
        "clean_copy_name": None,
    }
    result["privacy"] = _build_media_privacy(result)

    # Copia limpia sin metadatos para imágenes (ideal para privacidad defensiva)
    if media_type == "image" and generate_clean_copy and out_dir and ext not in (".heic", ".heif"):
        clean_name = f"limpia_{Path(original_name or path.name).stem}{ext}"
        if strip_image_metadata(path, out_dir / clean_name):
            result["clean_copy_name"] = clean_name

    return result


def format_media_report(res: dict) -> dict:
    """Formatea el resultado de `inspect_media` en secciones listas para HTML y PDF."""
    priv = res.get("privacy") or {}
    gps = res.get("gps") or {}

    resumen = [
        f"Archivo: {res.get('filename')}",
        f"Tipo: {res.get('media_type', '—').upper()} ({res.get('mime_type')})",
        f"Tamaño: {res.get('size_human')}",
        f"Motores usados: {', '.join(res.get('engines') or []) or 'ninguno'}",
        f"SHA-256: {(res.get('hashes') or {}).get('sha256', '—')}",
        f"MD5: {(res.get('hashes') or {}).get('md5', '—')}",
    ]
    for k, v in (res.get("technical") or {}).items():
        resumen.append(f"{k}: {v}")

    secciones = {
        "Resumen del archivo": resumen,
        "Evaluación de privacidad": [
            f"Riesgo en metadatos: {priv.get('level', 'BAJO')}",
            *(priv.get("findings") or []),
            *(priv.get("recommendations") or []),
        ],
    }

    if gps.get("latitude") is not None:
        secciones["Ubicación GPS detectada"] = [
            f"Coordenadas: {gps['latitude']}, {gps['longitude']}",
            *(["Altitud: " + str(gps["altitude_m"]) + " m"] if gps.get("altitude_m") is not None else []),
            f"OpenStreetMap: {gps['openstreetmap_url']}",
            f"Google Maps: {gps['google_maps_url']}",
        ]

    meta_lines = [f"{k}: {v}" for k, v in sorted((res.get("metadata") or {}).items())]
    secciones[f"Metadatos extraídos ({len(meta_lines)})"] = meta_lines or ["(sin metadatos embebidos)"]
    return secciones

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import math
import httpx
from typing import Dict, Any, List, Optional

app = FastAPI(title="Risk Info API", version="1.6")

# =========================
# Utilidades comunes
# =========================

def to_webmercator(lat: float, lon: float):
    """Convierte lat/lon (grados WGS84) a Web Mercator (EPSG:3857)."""
    R = 6378137.0
    x = lon * (math.pi / 180.0) * R
    y = math.log(math.tan((math.pi / 4.0) + (lat * math.pi / 360.0))) * R
    return x, y


def build_gfi_url(
    wms_url: str,
    layer: str,
    bbox: str,
    crs: str,
    width: int = 256,
    height: int = 256,
    info_format: str = "application/json",
    styles: Optional[str] = None,
    feature_count: int = 10,
    vendor_params: Optional[Dict[str, Any]] = None,
) -> str:
    base = (
        f"{wms_url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
        f"&LAYERS={layer}&QUERY_LAYERS={layer}"
        f"&CRS={crs}&BBOX={bbox}"
        f"&WIDTH={width}&HEIGHT={height}"
        f"&I={width//2}&J={height//2}"
        f"&INFO_FORMAT={info_format}"
        f"&FEATURE_COUNT={feature_count}"
    )
    if styles:
        base += f"&STYLES={styles}"
    if vendor_params:
        for k, v in vendor_params.items():
            base += f"&{k}={v}"
    return base


async def fetch_any(client: httpx.AsyncClient, urls: List[str]) -> Dict[str, Any]:
    last_err = None
    for u in urls:
        try:
            r = await client.get(u, follow_redirects=True, timeout=25.0)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"raw": r.text}
        except Exception as e:
            last_err = str(e)
    return {"error": last_err or "unknown error"}


def remove_geometry_from_geojson(obj: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return obj
    if obj.get("type") == "FeatureCollection":
        feats = []
        for f in obj.get("features", []):
            if isinstance(f, dict):
                feats.append({k: v for k, v in f.items() if k != "geometry"})
        return {"type": "FeatureCollection", "features": feats}
    if obj.get("type") == "Feature":
        return {k: v for k, v in obj.items() if k != "geometry"}
    return obj


# =========================
# Normalizadores / Parsers
# =========================

def parse_incendios_summary(obj: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(obj, dict) or obj.get("error"):
        return {"resumen": "desconocido", "fuente": "MITECO", "raw": obj}

    fc = remove_geometry_from_geojson(obj)
    feats = fc.get("features", []) if isinstance(fc, dict) else []
    if not feats:
        return {"resumen": "sin_datos", "fuente": "MITECO"}

    props = feats[0].get("properties", feats[0])
    municipio = (
        props.get("municipio") or props.get("MUNICIPIO")
        or props.get("name") or props.get("NAMEUNIT")
        or props.get("NOMBRE")
    )
    freq = props.get("frecuencia") or props.get("N_INCENDIOS") or props.get("num_incendios")

    nivel = None
    try:
        if freq is not None:
            f = float(freq)
            if f == 0:
                nivel = "ninguno"
            elif f < 5:
                nivel = "bajo"
            elif f < 20:
                nivel = "medio"
            else:
                nivel = "alto"
    except Exception:
        pass

    out = {"fuente": "MITECO", "municipio": municipio}
    if nivel:
        out["riesgo_incendios"] = nivel
        out["frecuencia_aprox"] = freq
    else:
        out["riesgo_incendios"] = "desconocido"
    out["props"] = props
    return out


NODATA = -3.4028234663852886e+38

def inundable_from_gray(fc: Dict[str, Any]) -> str:
    try:
        feats = fc.get("features", [])
        if not feats:
            return "nodata"
        gray = feats[0].get("properties", {}).get("GRAY_INDEX", None)
        if gray is None:
            return "no_inundable"
        if isinstance(gray, (int, float)):
            if gray == 0:
                return "no_inundable"
            if abs(gray - NODATA) < 1e-6:
                return "nodata"
            return "inundable"
        g = float(gray)
        if g == 0:
            return "no_inundable"
        if abs(g - NODATA) < 1e-6:
            return "nodata"
        return "inundable"
    except Exception:
        return "nodata"


def parse_sismico_summary(obj: Dict[str, Any]) -> Dict[str, Any]:
    try:
        fc = remove_geometry_from_geojson(obj)
        feats = fc.get("features", [])
        if not feats:
            return {"riesgo_sismico": "desconocido"}
        props = feats[0].get("properties", feats[0])
        pga = None
        for key in ("PGA", "pga", "aceleracion", "ACCEL", "amax"):
            if key in props:
                try:
                    pga = float(props[key])
                    break
                except Exception:
                    pass
        if pga is None:
            return {"riesgo_sismico": "sin_riesgo"}
        if pga < 0.04:
            nivel = "bajo"
        elif pga < 0.08:
            nivel = "medio"
        else:
            nivel = "alto"
        return {"pga": pga, "riesgo_sismico": nivel}
    except Exception:
        return {"riesgo_sismico": "desconocido"}


def parse_desertificacion(fc: Dict[str, Any], tipo: str) -> Dict[str, Any]:
    try:
        feats = fc.get("features", [])
        if not feats:
            return {"tipo": tipo, "nivel": "nodata"}
        gray = feats[0].get("properties", {}).get("GRAY_INDEX")
        if gray is None:
            return {"tipo": tipo, "nivel": "sin_riesgo"}
        val = float(gray)
        if val < 0:
            nivel = "sin riesgo"
        elif val < 50:
            nivel = "bajo"
        elif val < 100:
            nivel = "medio"
        else:
            nivel = "alto"
        return {"tipo": tipo, "nivel": nivel, "valor": val}
    except Exception:
        return {"tipo": tipo, "nivel": "nodata"}


# =========================
# Core fetch
# =========================

async def fetch_all_risks(lat: float, lon: float) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    async with httpx.AsyncClient() as client:
        # BBOX comunes
        d_deg = 0.20
        bbox_c84_small = f"{lon - 0.02},{lat - 0.02},{lon + 0.02},{lat + 0.02}"

        # ----- INCENDIOS -----
        bbox_crs84 = f"{lon - d_deg},{lat - d_deg},{lon + d_deg},{lat + d_deg}"
        incendios_urls = [
            build_gfi_url(
                "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                "NZ.HazardArea", bbox=bbox_crs84, crs="CRS:84",
                info_format="application/json", styles="Biodiversidad_Incendios",
            )
        ]
        results["incendios"] = await fetch_any(client, incendios_urls)

        # ----- INUNDACIONES -----
        vendor = {"FI_POINT_TOLERANCE": 8, "FI_LINE_TOLERANCE": 4, "FI_POLYGON_TOLERANCE": 4}

        results["inundacion_fluvial"] = {}
        for periodo in ["T10", "T100", "T500"]:
            url_fluvial = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Fluvial{periodo}", bbox=bbox_c84_small, crs="CRS:84",
                info_format="application/json", vendor_params=vendor
            )
            results["inundacion_fluvial"][periodo] = await fetch_any(client, [url_fluvial])

        results["inundacion_marina"] = {}
        for periodo in ["T100", "T500"]:
            url_marina = build_gfi_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Marina{periodo}", bbox=bbox_c84_small, crs="CRS:84",
                info_format="application/json", vendor_params=vendor
            )
            results["inundacion_marina"][periodo] = await fetch_any(client, [url_marina])

        # ----- SÍSMICO -----
        url_sismico = build_gfi_url(
            "https://www.ign.es/wms-inspire/geofisica",
            "HazardArea2002.NCSE-02", bbox=bbox_c84_small, crs="CRS:84",
            info_format="application/json"
        )
        results["sismico"] = await fetch_any(client, [url_sismico])

        # ----- DESERTIFICACIÓN -----
        url_des_pot = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionPotencial/wms.aspx",
            "NZ.HazardArea", bbox=bbox_c84_small, crs="CRS:84",
            info_format="application/json"
        )
        url_des_lam = build_gfi_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionLaminarRaster/wms.aspx",
            "NZ.HazardArea", bbox=bbox_c84_small, crs="CRS:84",
            info_format="application/json"
        )
        results["desertificacion_potencial"] = await fetch_any(client, [url_des_pot])
        results["desertificacion_laminar"] = await fetch_any(client, [url_des_lam])

    return results


# =========================
# Endpoints
# =========================

@app.get("/api/risk_clean")
async def api_risk_clean(
    lat: float = Query(..., description="Latitud WGS84 (grados)"),
    lon: float = Query(..., description="Longitud WGS84 (grados)"),
):
    try:
        raw = await fetch_all_risks(lat, lon)

        out = {"lat": lat, "lon": lon, "resumen": {}}

        # Incendios
        out["resumen"]["incendios"] = parse_incendios_summary(raw.get("incendios", {}))

        # Inundación
        inf = raw.get("inundacion_fluvial", {})
        out["resumen"]["inundacion_fluvial"] = {k: inundable_from_gray(v) for k, v in inf.items()}

        im = raw.get("inundacion_marina", {})
        out["resumen"]["inundacion_marina"] = {k: inundable_from_gray(v) for k, v in im.items()}

        # Sísmico
        out["resumen"]["sismico"] = parse_sismico_summary(raw.get("sismico", {}))

        # Desertificación
        out["resumen"]["desertificacion"] = {
            "potencial": parse_desertificacion(raw.get("desertificacion_potencial", {}), "potencial"),
            "laminar": parse_desertificacion(raw.get("desertificacion_laminar", {}), "laminar"),
        }

        # Sin geometría
        out["sin_geometria"] = {
            "incendios": remove_geometry_from_geojson(raw.get("incendios", {})),
            "inundacion_fluvial": {k: remove_geometry_from_geojson(v) for k, v in inf.items()},
            "inundacion_marina": {k: remove_geometry_from_geojson(v) for k, v in im.items()},
            "sismico": remove_geometry_from_geojson(raw.get("sismico", {})),
            "desertificacion_potencial": remove_geometry_from_geojson(raw.get("desertificacion_potencial", {})),
            "desertificacion_laminar": remove_geometry_from_geojson(raw.get("desertificacion_laminar", {})),
        }

        return out

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

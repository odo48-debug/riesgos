from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import math
import httpx

app = FastAPI(title="Risk Info API", version="1.4")

# -----------------------------
# Utilidades
# -----------------------------
def to_webmercator(lat: float, lon: float):
    """Convierte lat/lon (grados WGS84) a Web Mercator (EPSG:3857)"""
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
    styles: str | None = None,
    feature_count: int = 10,
) -> str:
    """
    Construye una URL GetFeatureInfo a partir de un BBOX ya calculado.
    Ojo: el orden del BBOX depende del CRS (CRS:84 / EPSG:4326 / EPSG:3857).
    - CRS:84 usa lon,lat (en grados)
    - EPSG:4326 usa lat,lon (en grados) [regla WMS 1.3.0]
    - EPSG:3857 usa lon/lat proyectado a metros (x,y)
    """
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
    return base


async def fetch_any(client: httpx.AsyncClient, urls: list[str]):
    """
    Intenta hacer GET a una lista de URLs en orden.
    Devuelve JSON si posible, o {raw: <texto>} si no es JSON.
    Si todas fallan, devuelve {error: "..."}.
    """
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


# -----------------------------
# Endpoint principal
# -----------------------------
@app.get("/api/risk")
async def get_all_risks(
    lat: float = Query(..., description="Latitud WGS84 (grados)"),
    lon: float = Query(..., description="Longitud WGS84 (grados)"),
):
    """
    Devuelve información consolidada de riesgos en el punto (lat, lon):
    - Incendios (MITECO)
    - Inundación fluvial y marina (IDEE)
    - Peligrosidad sísmica (IGN)
    """
    try:
        results = {}
        async with httpx.AsyncClient() as client:
            # --------------------------
            # 🔥 Incendios (MITECO)
            # Intentos en cascada para evitar 500:
            # 1) CRS:84 con JSON/HTML/PLAIN
            # 2) EPSG:4326 con JSON/HTML
            # 3) EPSG:3857 (proyectado) con JSON/HTML
            # Además incluimos STYLES=Biodiversidad_Incendios
            # --------------------------
            d_deg = 0.20  # ventana en grados (amplia para no caer fuera de municipio)
            bbox_crs84 = f"{lon - d_deg},{lat - d_deg},{lon + d_deg},{lat + d_deg}"  # lon,lat
            x, y = to_webmercator(lat, lon)
            d_m = 15000.0  # 15 km en metros para 3857
            bbox_3857 = f"{x - d_m},{y - d_m},{x + d_m},{y + d_m}"  # x,y metros
            bbox_epsg4326 = f"{lat - d_deg},{lon - d_deg},{lat + d_deg},{lon + d_deg}"  # lat,lon

            incendios_urls = [
                # CRS:84 (lon,lat) - JSON, HTML, PLAIN
                build_gfi_url(
                    "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                    "NZ.HazardArea",
                    bbox=bbox_crs84,
                    crs="CRS:84",
                    info_format="application/json",
                    styles="Biodiversidad_Incendios",
                ),
                build_gfi_url(
                    "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                    "NZ.HazardArea",
                    bbox=bbox_crs84,
                    crs="CRS:84",
                    info_format="text/html",
                    styles="Biodiversidad_Incendios",
                ),
                build_gfi_url(
                    "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                    "NZ.HazardArea",
                    bbox=bbox_crs84,
                    crs="CRS:84",
                    info_format="text/plain",
                    styles="Biodiversidad_Incendios",
                ),
                # EPSG:4326 (lat,lon) - JSON, HTML
                build_gfi_url(
                    "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                    "NZ.HazardArea",
                    bbox=bbox_epsg4326,
                    crs="EPSG:4326",
                    info_format="application/json",
                    styles="Biodiversidad_Incendios",
                ),
                build_gfi_url(
                    "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                    "NZ.HazardArea",
                    bbox=bbox_epsg4326,
                    crs="EPSG:4326",
                    info_format="text/html",
                    styles="Biodiversidad_Incendios",
                ),
                # EPSG:3857 (x,y en metros) - JSON, HTML
                build_gfi_url(
                    "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                    "NZ.HazardArea",
                    bbox=bbox_3857,
                    crs="EPSG:3857",
                    info_format="application/json",
                    styles="Biodiversidad_Incendios",
                ),
                build_gfi_url(
                    "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
                    "NZ.HazardArea",
                    bbox=bbox_3857,
                    crs="EPSG:3857",
                    info_format="text/html",
                    styles="Biodiversidad_Incendios",
                ),
            ]
            results["incendios"] = await fetch_any(client, incendios_urls)

            # --------------------------
            # 🌊 Inundación (IDEE) – CRS:84 lon,lat (aceptan bien grados)
            # --------------------------
            results["inundacion_fluvial"] = {}
            results["inundacion_marina"] = {}

            bbox_c84_small = f"{lon - 0.05},{lat - 0.05},{lon + 0.05},{lat + 0.05}"

            for periodo in ["T10", "T100", "T500"]:
                url_fluvial = build_gfi_url(
                    "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                    f"NZ.Flood.Fluvial{periodo}",
                    bbox=bbox_c84_small,
                    crs="CRS:84",
                    info_format="application/json",
                )
                results["inundacion_fluvial"][periodo] = await fetch_any(client, [url_fluvial])

            for periodo in ["T100", "T500"]:
                url_marina = build_gfi_url(
                    "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                    f"NZ.Flood.Marina{periodo}",
                    bbox=bbox_c84_small,
                    crs="CRS:84",
                    info_format="application/json",
                )
                results["inundacion_marina"][periodo] = await fetch_any(client, [url_marina])

            # --------------------------
            # 🌍 Sísmico (IGN) – CRS:84 lon,lat
            # --------------------------
            url_sismico = build_gfi_url(
                "https://www.ign.es/wms-inspire/geofisica",
                "HazardArea2002.NCSE-02",
                bbox=bbox_c84_small,
                crs="CRS:84",
                info_format="application/json",
            )
            results["sismico"] = await fetch_any(client, [url_sismico])

        return {"lat": lat, "lon": lon, "riesgos": results}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

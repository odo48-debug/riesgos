from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import httpx

app = FastAPI(title="Risk Info API", version="1.3")

# Helper para armar la URL de GetFeatureInfo
def build_getfeatureinfo_url(
    wms_url: str,
    layer: str,
    lat: float,
    lon: float,
    width: int = 256,
    height: int = 256,
    crs: str = "EPSG:3857",
    info_format: str = "application/json"
) -> str:
    """
    Construye la URL de GetFeatureInfo centrada en (lat, lon).
    - EPSG:4326 → orden lat,lon
    - otros CRS (ej. EPSG:3857) → orden lon,lat
    """
    delta = 0.05  # ventana alrededor del punto

    if crs == "EPSG:4326":
        bbox = f"{lat - delta},{lon - delta},{lat + delta},{lon + delta}"  # lat,lon
    else:
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"  # lon,lat

    return (
        f"{wms_url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
        f"&LAYERS={layer}&QUERY_LAYERS={layer}"
        f"&CRS={crs}&BBOX={bbox}"
        f"&WIDTH={width}&HEIGHT={height}"
        f"&I={width//2}&J={height//2}"
        f"&INFO_FORMAT={info_format}"
    )

# Función para consultar un WMS
async def fetch_wms(url: str):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"raw": r.text}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/risk")
async def get_all_risks(
    lat: float = Query(..., description="Latitud en WGS84"),
    lon: float = Query(..., description="Longitud en WGS84"),
):
    try:
        results = {}

        # 🔥 Riesgo de incendios → usar EPSG:4326 (grados)
        url_incendios = build_getfeatureinfo_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
            "NZ.HazardArea", lat, lon, crs="EPSG:4326"
        )
        results["incendios"] = await fetch_wms(url_incendios)

        # 🌊 Riesgo inundación (fluvial) → usar EPSG:3857 (Web Mercator)
        results["inundacion_fluvial"] = {}
        for periodo in ["T10", "T100", "T500"]:
            url = build_getfeatureinfo_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Fluvial{periodo}", lat, lon, crs="EPSG:3857"
            )
            results["inundacion_fluvial"][periodo] = await fetch_wms(url)

        # 🌊 Riesgo inundación (marina) → usar EPSG:3857
        results["inundacion_marina"] = {}
        for periodo in ["T100", "T500"]:
            url = build_getfeatureinfo_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Marina{periodo}", lat, lon, crs="EPSG:3857"
            )
            results["inundacion_marina"][periodo] = await fetch_wms(url)

        # 🌍 Riesgo sísmico → usar EPSG:3857
        url_sismico = build_getfeatureinfo_url(
            "https://www.ign.es/wms-inspire/geofisica",
            "HazardArea2002.NCSE-02", lat, lon, crs="EPSG:3857"
        )
        results["sismico"] = await fetch_wms(url_sismico)

        return {"lat": lat, "lon": lon, "riesgos": results}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

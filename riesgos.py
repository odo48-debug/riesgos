from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import httpx

app = FastAPI(title="Risk Info API", version="1.2")


# ---------------------------
# Helpers
# ---------------------------

def build_getfeatureinfo_url(wms_url: str, layer: str, lat: float, lon: float,
                             width: int = 256, height: int = 256,
                             crs: str = "EPSG:4326") -> str:
    """
    Construye la URL para GetFeatureInfo en un punto.
    """
    delta = 0.01  # ventana alrededor del punto
    # En WMS 1.3.0 con EPSG:4326, el orden es lat,lon
    bbox = f"{lat - delta},{lon - delta},{lat + delta},{lon + delta}"

    return (
        f"{wms_url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
        f"&LAYERS={layer}&QUERY_LAYERS={layer}"
        f"&CRS={crs}&BBOX={bbox}"
        f"&WIDTH={width}&HEIGHT={height}"
        f"&I={width//2}&J={height//2}"
        f"&INFO_FORMAT=application/json"
    )


async def fetch_wms(url: str):
    """
    Descarga datos WMS GetFeatureInfo.
    """
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


# ---------------------------
# Interpretadores de riesgo
# ---------------------------

def interpretar_incendios(data):
    props = {}
    riesgo = "desconocido"
    try:
        features = data.get("features", [])
        if features:
            props = features[0].get("properties", {})
            freq = int(props.get("Frecuencia Incendios Forestales", 0))

            if freq == 0:
                riesgo = "sin incendios"
            elif 1 <= freq <= 5:
                riesgo = "muy bajo"
            elif 6 <= freq <= 10:
                riesgo = "bajo"
            elif 11 <= freq <= 25:
                riesgo = "medio"
            elif 26 <= freq <= 50:
                riesgo = "medio-alto"
            elif 51 <= freq <= 100:
                riesgo = "alto"
            elif 101 <= freq <= 500:
                riesgo = "muy alto"
            elif 501 <= freq <= 1000:
                riesgo = "extremo"
            elif 1001 <= freq <= 1511:
                riesgo = "extremo+"
            else:
                riesgo = "máximo"
    except Exception:
        pass

    return {"fuente": "MITECO", "riesgo_incendios": riesgo, "props": props}


def interpretar_inundacion(data):
    try:
        features = data.get("features", [])
        if not features:
            return "nodata"
        val = features[0].get("properties", {}).get("GRAY_INDEX")
        if val is None:
            return "nodata"
        return "inundable" if val > -9999 and val != 0 else "no_inundable"
    except Exception:
        return "nodata"


def interpretar_sismico(data):
    try:
        features = data.get("features", [])
        if features:
            props = features[0].get("properties", {})
            accel = props.get("aceleracion")
            if accel:
                try:
                    accel_val = float(accel)
                    if accel_val < 0.04:
                        riesgo = "bajo"
                    elif accel_val < 0.08:
                        riesgo = "medio"
                    else:
                        riesgo = "alto"
                    return {"pga": accel_val, "riesgo_sismico": riesgo}
                except:
                    pass
        return {"riesgo_sismico": "desconocido"}
    except Exception:
        return {"riesgo_sismico": "desconocido"}


def interpretar_desertificacion(data, tipo="desconocido"):
    try:
        features = data.get("features", [])
        if features:
            props = features[0].get("properties", {})
            val = props.get("GRAY_INDEX")
            if val is not None and isinstance(val, (int, float)):
                if val < 0:
                    nivel = "sin riesgo"
                elif val < 50:
                    nivel = "bajo"
                elif val < 100:
                    nivel = "medio"
                else:
                    nivel = "alto"
                return {"tipo": tipo, "nivel": nivel, "valor": val}
        return {"tipo": tipo, "nivel": "nodata"}
    except Exception:
        return {"tipo": tipo, "nivel": "nodata"}


# ---------------------------
# Endpoints
# ---------------------------

@app.get("/api/risk")
async def get_all_risks(
    lat: float = Query(..., description="Latitud en WGS84"),
    lon: float = Query(..., description="Longitud en WGS84"),
):
    try:
        results = {}

        # Incendios
        url_incendios = build_getfeatureinfo_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/Incendios/2006_2015",
            "NZ.HazardArea", lat, lon
        )
        results["incendios"] = await fetch_wms(url_incendios)

        # Inundación fluvial
        results["inundacion_fluvial"] = {}
        for periodo in ["T10", "T100", "T500"]:
            url = build_getfeatureinfo_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Fluvial{periodo}", lat, lon
            )
            results["inundacion_fluvial"][periodo] = await fetch_wms(url)

        # Inundación marina
        results["inundacion_marina"] = {}
        for periodo in ["T100", "T500"]:
            url = build_getfeatureinfo_url(
                "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones",
                f"NZ.Flood.Marina{periodo}", lat, lon
            )
            results["inundacion_marina"][periodo] = await fetch_wms(url)

        # Sísmico
        url_sismico = build_getfeatureinfo_url(
            "https://www.ign.es/wms-inspire/geofisica",
            "HazardArea2002.NCSE-02", lat, lon
        )
        results["sismico"] = await fetch_wms(url_sismico)

        # Desertificación
        url_des_pot = build_getfeatureinfo_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionPotencial/wms.aspx",
            "NZ.HazardArea", lat, lon
        )
        results["desertificacion_potencial"] = await fetch_wms(url_des_pot)

        url_des_lam = build_getfeatureinfo_url(
            "https://wms.mapama.gob.es/sig/Biodiversidad/INESErosionLaminarRaster/wms.aspx",
            "NZ.HazardArea", lat, lon
        )
        results["desertificacion_laminar"] = await fetch_wms(url_des_lam)

        return {"lat": lat, "lon": lon, "riesgos": results}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/risk_clean")
async def get_clean_risks(
    lat: float = Query(..., description="Latitud en WGS84"),
    lon: float = Query(..., description="Longitud en WGS84"),
):
    raw = await get_all_risks(lat, lon)
    if isinstance(raw, JSONResponse):
        return raw
    riesgos = raw["riesgos"]

    resumen = {
        "incendios": interpretar_incendios(riesgos["incendios"]),
        "inundacion_fluvial": {p: interpretar_inundacion(riesgos["inundacion_fluvial"][p])
                               for p in riesgos["inundacion_fluvial"]},
        "inundacion_marina": {p: interpretar_inundacion(riesgos["inundacion_marina"][p])
                              for p in riesgos["inundacion_marina"]},
        "sismico": interpretar_sismico(riesgos["sismico"]),
        "desertificacion": {
            "potencial": interpretar_desertificacion(riesgos["desertificacion_potencial"], "potencial"),
            "laminar": interpretar_desertificacion(riesgos["desertificacion_laminar"], "laminar")
        }
    }

    return {
        "lat": lat,
        "lon": lon,
        "resumen": resumen,
        "sin_geometria": riesgos
    }

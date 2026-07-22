import httpx
from . import tool

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

@tool("meteo", "Donne la météo actuelle pour une ville. Paramètre : lieu (nom de la ville).")
async def get_weather(lieu: str) -> str:
    if not lieu or not lieu.strip():
        return "Pour quelle ville veux-tu la météo ?"
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            geo = await c.get(GEO_URL, params={"name": lieu, "count": 1, "language": "fr", "format": "json"})
            geo.raise_for_status()
            geo_data = geo.json()
            results = geo_data.get("results")
            if not results:
                return f"Je n'ai pas trouvé la ville « {lieu} »."
            loc = results[0]
            lat, lon = loc["latitude"], loc["longitude"]
            city = loc.get("name", lieu)
            country = loc.get("country", "")
        except Exception as e:
            return f"Erreur de géocodage pour « {lieu} » : {e}"
        try:
            w = await c.get(WEATHER_URL, params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "timezone": "auto"
            })
            w.raise_for_status()
            data = w.json()["current"]
            temp = data["temperature_2m"]
            feels = data["apparent_temperature"]
            hum = data["relative_humidity_2m"]
            wind = data["wind_speed_10m"]
            code = data["weather_code"]
            desc = _wmo_desc(code)
            return f"À {city}{', ' + country if country else ''} : {desc.lower()}, {temp:.0f}°C (ressenti {feels:.0f}°C), humidité {hum}%, vent {wind:.0f} km/h."
        except Exception as e:
            return f"Erreur météo pour {city} : {e}"

WMO = {
    0:"Ciel dégagé",1:"Principalement dégagé",2:"Partiellement nuageux",3:"Nuageux",
    45:"Brouillard",48:"Brouillard givrant",51:"Bruine légère",53:"Bruine modérée",55:"Bruine dense",
    56:"Bruine verglaçante légère",57:"Bruine verglaçante dense",61:"Pluie légère",63:"Pluie modérée",65:"Pluie forte",
    66:"Pluie verglaçante légère",67:"Pluie verglaçante forte",71:"Neige légère",73:"Neige modérée",75:"Neige forte",
    77:"Grains de neige",80:"Averses légères",81:"Averses modérées",82:"Averses fortes",
    85:"Averses de neige légères",86:"Averses de neige fortes",95:"Orage",96:"Orage avec grêle légère",99:"Orage avec grêle forte"
}

def _wmo_desc(code: int) -> str:
    return WMO.get(code, "Conditions inconnues")

"""Module TTS (Text-to-Speech) pour Alex — utilise Edge TTS."""
import asyncio
import edge_tts
import io

# Voix françaises disponibles (Edge TTS)
VOICES = {
    "denise": {"id": "fr-FR-DeniseNeural", "name": "Denise — française, douce", "gender": "Female", "lang": "fr-FR"},
    "charline": {"id": "fr-FR-EloiseNeural", "name": "Charline — française, pétillante", "gender": "Female", "lang": "fr-FR"},
    "henri": {"id": "fr-FR-HenriNeural", "name": "Henri — français, masculin", "gender": "Male", "lang": "fr-FR"},
    "remy": {"id": "fr-FR-RemyMultilingualNeural", "name": "Rémy — français, multilingue", "gender": "Male", "lang": "fr-FR"},
    "vivienne": {"id": "fr-FR-VivienneMultilingualNeural", "name": "Vivienne — française, multilingue", "gender": "Female", "lang": "fr-FR"},
    "jean": {"id": "fr-CA-JeanNeural", "name": "Jean — canadien, jeune", "gender": "Male", "lang": "fr-CA"},
    "antoine": {"id": "fr-CA-AntoineNeural", "name": "Antoine — canadien, mature", "gender": "Male", "lang": "fr-CA"},
    "ariane": {"id": "fr-CH-ArianeNeural", "name": "Ariane — suisse, féminin", "gender": "Female", "lang": "fr-CH"},
    "fabrice": {"id": "fr-CH-FabriceNeural", "name": "Fabrice — suisse, masculin", "gender": "Male", "lang": "fr-CH"},
}

DEFAULT_VOICE = "denise"


def list_voices():
    """Retourne la liste des voix disponibles."""
    return [
        {"id": vid, "name": v["name"], "gender": v["gender"], "lang": v.get("lang", "fr-FR")}
        for vid, v in VOICES.items()
    ]


async def tts_generate(text: str, voice_id: str = DEFAULT_VOICE) -> bytes:
    """Génère un fichier audio MP3 à partir du texte."""
    voice_info = VOICES.get(voice_id, VOICES[DEFAULT_VOICE])
    voice_name = voice_info["id"]

    communicate = edge_tts.Communicate(text, voice_name)
    audio_data = io.BytesIO()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.write(chunk["data"])

    return audio_data.getvalue()

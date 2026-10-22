"""Telefonía: entrada de llamadas reales al voicebot.

Capas:
- `audio.py`: conversiones μ-law 8 kHz (red telefónica) ↔ PCM16 16 kHz (STT/TTS).
- `tts.py`: síntesis de voz en servidor (Piper local; Workers AI como alternativa).
- `bridge.py`: puente independiente del proveedor entre el audio de la llamada y el grafo.
- `twilio.py`: adaptador Twilio (webhook TwiML + Media Streams bidireccional).
- `configure.py`: deja el número apuntando a esta API (`python -m building_call.telephony.configure`).

El proveedor está *Pendiente* en la bóveda: Twilio es la primera implementación (Provisional). El
puente no conoce a Twilio, así que otro proveedor solo necesita su propio adaptador.
"""

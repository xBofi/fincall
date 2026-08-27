# Consola de validación

React 19 + TypeScript + Vite + Ant Design. Simula una llamada con el voicebot y mide la latencia.

```powershell
npm install
npm run dev      # http://localhost:5173 (proxy /api -> http://127.0.0.1:8000)
npm run build
```

Requiere el backend en marcha (`cd ../backend; uv run building-call`). Micrófono y reconocimiento
de voz del navegador solo en Chrome/Edge.

## Pipeline de voz (`src/voz.ts`)

- **Captura con VAD**: `MediaRecorder` + `AudioContext`; fin de turno por silencio (por defecto
  550 ms, ajustable). `finVozAt` es el inicio de ese silencio.
- **STT**: `navegador` (Web Speech API) o `servidor` (faster-whisper vía `POST /api/llamadas/{id}/audio`).
- **TTS**: `SpeechSynthesis`; el instante `onstart` marca el inicio real del audio del asistente.
- **Barge-in**: si el vecino habla mientras el asistente responde, se corta la locución.
- **Manos libres**: tras cada respuesta vuelve a escuchar automáticamente.

## Métricas (pestaña "Latencia")

`respuesta_ms` = `ttsInicioAt - finVozAt`, con desglose VAD / STT / servidor (LLM) / red / TTS.
Umbrales: ≤700 excelente · ≤1200 objetivo · ≤1500 tolerable · ≤2000 mal · >2000 muy mal. Cada
turno se envía a `POST /api/llamadas/{id}/metricas` para agregarlo en el servidor (`GET /api/metricas`).

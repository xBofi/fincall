import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Con `BC_TUNEL=1` la consola se sirve detrás de un túnel https (ngrok) para poder usarla fuera
// de casa. Dos cosas cambian: hay que atender también en IPv4 —el túnel se conecta a
// 127.0.0.1:5173 y por defecto Vite solo escucha en ::1— y el cliente de HMR debe apuntar al 443
// público del túnel en vez de al puerto local. Sin túnel todo se queda como estaba.
const tunel = process.env.BC_TUNEL === '1'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: tunel ? '127.0.0.1' : undefined,
    // Vite rechaza los Host que no conoce; sin esto el túnel responde "Blocked request"
    allowedHosts: ['.ngrok-free.app', '.ngrok.app', '.ngrok.io'],
    hmr: tunel ? { protocol: 'wss', clientPort: 443 } : undefined,
    proxy: {
      // Mismo origen para la API y el WebSocket de voz: así no hace falta CORS ni tocar la cookie
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true },
      // Señalización de LiveKit por el mismo origen (BC_LIVEKIT_URL_CONSOLA=/): un solo túnel
      '/rtc': { target: 'http://127.0.0.1:7880', changeOrigin: true, ws: true },
    },
  },
})

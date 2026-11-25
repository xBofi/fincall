import { createReadStream, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * En desarrollo Vite sirve el worker de ELK como un módulo más: le añade un mapa de fuentes en
 * línea (1,5 MB → 10 MB) y `Cache-Control: no-cache`, así que cada visita a «Flujo» lo volvía a
 * bajar entero (a través del túnel, varios segundos). Aquí se sirve el fichero tal cual y con
 * caché larga: no cambia sin cambiar la versión de elkjs, que va en la ruta. El build no cambia.
 */
function elkWorkerAsIs(): Plugin {
  const file = createRequire(import.meta.url).resolve('elkjs/lib/elk-worker.min.js')
  return {
    name: 'elk-worker-as-is',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        // Solo la petición del worker (sin consulta): `?url` es el import que da su dirección
        if (!req.url?.endsWith('/elkjs/lib/elk-worker.min.js')) return next()
        res.setHeader('Content-Type', 'text/javascript')
        res.setHeader('Content-Length', statSync(file).size)
        res.setHeader('Cache-Control', 'public, max-age=31536000, immutable')
        createReadStream(file).pipe(res)
      })
    },
  }
}

// Con `BC_TUNEL=1` la consola se sirve detrás de un túnel https (ngrok) para poder usarla fuera
// de casa. Dos cosas cambian: hay que atender también en IPv4 —el túnel se conecta a
// 127.0.0.1:5173 y por defecto Vite solo escucha en ::1— y el cliente de HMR debe apuntar al 443
// público del túnel en vez de al puerto local. Sin túnel todo se queda como estaba.
const tunel = process.env.BC_TUNEL === '1'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), elkWorkerAsIs()],
  server: {
    port: 5173,
    host: tunel ? '127.0.0.1' : undefined,
    // Vite rechaza los Host que no conoce; sin esto el túnel responde "Blocked request"
    allowedHosts: ['.ngrok-free.app', '.ngrok-free.dev', '.ngrok.app', '.ngrok.io'],
    hmr: tunel ? { protocol: 'wss', clientPort: 443 } : undefined,
    proxy: {
      // Mismo origen para la API y el WebSocket de voz: así no hace falta CORS ni tocar la cookie
      // BC_API_TARGET: otra API (p. ej. una segunda copia en :8100 sin parar la demo)
      '/api': { target: process.env.BC_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true, ws: true },
      // Señalización de LiveKit por el mismo origen (BC_LIVEKIT_CONSOLE_URL=/): un solo túnel
      '/rtc': { target: 'http://127.0.0.1:7880', changeOrigin: true, ws: true },
    },
  },
})

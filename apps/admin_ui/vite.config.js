import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5174,
    proxy: {
      '/health': 'http://localhost:8002',
      '/robots': 'http://localhost:8002',
      '/map':    'http://localhost:8002',
      '/ws': { target: 'ws://localhost:8002', ws: true },
      '/arm-server': {
        target: 'http://192.168.1.115:8002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/arm-server/, ''),
      },
    },
  },
})

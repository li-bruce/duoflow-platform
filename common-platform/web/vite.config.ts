import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
// Server-side only. Set this explicitly when running another isolated backend.
const backendUrl = loadEnv('development', '.', 'PLATFORM_').PLATFORM_BACKEND_URL || 'http://127.0.0.1:8030';
export default defineConfig({
  resolve: {
    // Installed packs live outside web/. Resolve React (including JSX runtime)
    // from the host so every component uses the same React instance.
    dedupe: ['react', 'react-dom'],
    alias: {
      '@platform/workflow-sdk': decodeURIComponent(new URL('./src/workflows/sdk.ts', import.meta.url).pathname).replace(/^\/([A-Za-z]:\/)/, '$1'),
      '@platform/interaction-sdk': decodeURIComponent(new URL('./src/interactions/sdk.ts', import.meta.url).pathname).replace(/^\/([A-Za-z]:\/)/, '$1'),
    },
  },
  plugins: [react()],
  server: {
    host: '127.0.0.1', port: 5173,
    fs: {allow: ['.', '../templates', '../flows/packs', '../components/packs'].map(path => decodeURIComponent(new URL(path, import.meta.url).pathname).replace(/^\/([A-Za-z]:\/)/, '$1'))},
    proxy: {'/api': {target: backendUrl, changeOrigin: true}},
  },
  build: {
    outDir: 'dist', sourcemap: false,
    rollupOptions: {output: {manualChunks(id) {
      if (id.includes('node_modules/@jsonforms/') || id.includes('node_modules/jsonschema/') || id.includes('node_modules/ajv') || id.includes('node_modules/lodash/')) return 'forms';
      if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/') || id.includes('node_modules/scheduler/')) return 'react';
    }}},
  },
});

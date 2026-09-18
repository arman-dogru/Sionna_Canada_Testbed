import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {viteStaticCopy} from 'vite-plugin-static-copy';

const cesiumSource = 'node_modules/cesium/Build/Cesium';

export default defineConfig({
  plugins: [
    react(),
    viteStaticCopy({
      targets: [
        {
          src: `${cesiumSource}/{ThirdParty,Workers,Assets,Widgets}/**/*`,
          dest: 'cesiumStatic',
          rename: {stripBase: 4},
        },
      ],
    }),
  ],
  define: {CESIUM_BASE_URL: JSON.stringify('/cesiumStatic')},
  server: {
    proxy: {'/v1': 'http://localhost:8000', '/health': 'http://localhost:8000'}
  }
});

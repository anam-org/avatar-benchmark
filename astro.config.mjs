// @ts-check
import { defineConfig } from 'astro/config';

import cloudflare from '@astrojs/cloudflare';
import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  output: 'server',
  adapter: cloudflare(),
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
    // Substituted as a literal at build time. See src/lib/api.ts for why
    // import.meta.env must not be read in worker code.
    define: {
      __WORKER_COMMIT__: JSON.stringify(process.env.PUBLIC_WORKER_COMMIT ?? 'dev'),
    },
  },
});
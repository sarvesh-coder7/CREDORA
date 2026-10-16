import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true
        }
      }
    },
    envPrefix: ['VITE_', 'SUPABASE_'],
    define: {
      '__SUPABASE_URL__': JSON.stringify(env.SUPABASE_URL || env.VITE_SUPABASE_URL || 'https://qjjewofsqjusfkwioquf.supabase.co'),
      '__SUPABASE_ANON_KEY__': JSON.stringify(env.SUPABASE_ANON_KEY || env.VITE_SUPABASE_ANON_KEY || 'sb_publishable_-9PKQZETYwZxIMcqlIVPgA_46FsGLpN')
    }
  };
});

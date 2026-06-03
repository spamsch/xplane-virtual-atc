<script>
  // Vendored IBM Plex Mono (Technical theme). Bundled by Vite from node_modules
  // so it ships in the build — no network call, no CSP font-src needed.
  import '@fontsource/ibm-plex-mono/latin-400.css';
  import '@fontsource/ibm-plex-mono/latin-500.css';
  import '@fontsource/ibm-plex-mono/latin-600.css';
  import '@fontsource/ibm-plex-mono/latin-700.css';

  let { children } = $props();
</script>

{@render children()}

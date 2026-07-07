/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API in production (e.g. https://api.darkships.org). Empty
      in dev, so requests hit /api and Vite proxies them to the backend. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

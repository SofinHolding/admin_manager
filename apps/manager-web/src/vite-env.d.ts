/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `password` (mặc định) | `oidc` */
  readonly VITE_AUTH_MODE?: string;
  readonly VITE_OIDC_CLIENT_ID?: string;
  readonly VITE_OIDC_REDIRECT_URI?: string;
  readonly VITE_OIDC_SCOPE?: string;
  readonly VITE_OIDC_AUTHORIZE_URL?: string;
  readonly VITE_OIDC_TOKEN_URL?: string;
  readonly VITE_OIDC_LOGOUT_URL?: string;
  readonly VITE_OIDC_ISSUER?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

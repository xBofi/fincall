# Console Access with Google and Microsoft

## Decision

Decided by Andreu on 2026-10-21. This replaces the 2026-08-29 rule "no external OAuth, internal
users with password":

- The console is entered **only** with Google or Microsoft (OpenID Connect). The username and
  password login is removed.
- Only `owner@example.com` gets in directly, as `owner`.
- Anyone else who signs in lands on a page that explains the product and lets them **request
  access**. The owner sees the request in an «Solicitudes» tab and grants it, with a role, or
  denies it.
- The console runs on **localhost only** for now (`http://localhost:5173`). Google and Microsoft
  only accept return addresses registered in advance.
- Access must persist: no daily re-login, no password that changes.

## Identity

- **Flow:** authorization code with PKCE.
  - `state`, `nonce` and the PKCE verifier travel in a short-lived HttpOnly cookie, signed with
    `BC_SESION_SECRETO` (10 minutes, path `/api/auth/oidc`).
  - The `id_token` is checked against the provider's JWKS: signature, `iss`, `aud`, `exp` and
    `nonce`.
- **Google:** issuer `https://accounts.google.com`. The email counts only with
  `email_verified=true`.
- **Microsoft:** the `common` authority, so both personal and work accounts work. The issuer
  must be `https://login.microsoftonline.com/{tid}/v2.0` for the token's own `tid`.
  - An email from Microsoft is not proof of ownership in a work tenant (the "nOAuth" problem).
    It counts as verified only for personal accounts (`tid` =
    `9188040d-6c67-4c5b-b112-36a304b66dad`) or with `xms_edov=true`.
- **Account key:** a person is `(proveedor, sub)`. The email is used only:
  - to recognise the owner (verified email == `BC_OWNER_EMAIL`), and
  - for display.
  Signing in with the same email at the other provider is a different identity until the owner
  grants it.

## Access states

`estado_acceso` on `Usuario`:

| State | Meaning | What the person sees |
|---|---|---|
| `sin_solicitar` | Signed in, has not asked | Landing + «Solicitar acceso» |
| `solicitado` | Asked, waiting for the owner | Landing: "Solicitud enviada" |
| `concedido` | Can use the console with their role | The console |
| `denegado` | The owner said no | Landing: "Acceso denegado" |

- `requiere()` only lets `concedido` and active users through.
- `/api/auth/estado` and `/api/auth/solicitar` work for any signed-in person.
- The owner email always comes in as `concedido`/`owner`. The last active owner cannot be
  removed (rule kept).

## Persistence

- Sessions live in the database (`BC_REPOSITORIO=postgres`), as today: an opaque token, only its
  hash stored.
- **30 days, sliding:** each use past the halfway point extends the session and the cookie.
  Signing out revokes it.
- Nothing about access depends on a password or on the API process staying up.

## API

| Route | Who | |
|---|---|---|
| `GET /api/auth/proveedores` | anyone | configured providers |
| `GET /api/auth/oidc/{p}/inicio` | anyone | redirect to the provider |
| `GET /api/auth/oidc/{p}/retorno` | anyone | callback: session + redirect to `/` |
| `GET /api/auth/estado` | anyone | person and `estado_acceso` |
| `POST /api/auth/solicitar` | signed in | `sin_solicitar`/`denegado` → `solicitado` |
| `POST /api/auth/logout` | signed in | revokes the session |
| `GET /api/usuarios` | owner | everyone, requests included |
| `POST /api/usuarios/{id}/conceder` `{rol}` | owner | grant with a role |
| `POST /api/usuarios/{id}/denegar` | owner | deny (revokes sessions) |
| `PATCH` / `DELETE /api/usuarios/{id}` | owner | as today |

Removed: `/api/auth/login`, `/api/auth/contrasena`, creating users with a password,
`/restablecer`, and the initial `admin`/`admin` owner.

## Configuration (`backend/.env`, never in notes or logs)

- `BC_URL_CONSOLA` (default `http://localhost:5173`): the return address is
  `{BC_URL_CONSOLA}/api/auth/oidc/{p}/retorno`.
- `BC_GOOGLE_CLIENT_ID`, `BC_GOOGLE_CLIENT_SECRET`.
- `BC_MICROSOFT_CLIENT_ID`, `BC_MICROSOFT_CLIENT_SECRET`.
- `BC_OWNER_EMAIL=owner@example.com`.

## Database

Migration `0006`, on `usuarios`:

- `hash_contrasena` becomes nullable;
- new columns `email`, `proveedor`, `sujeto`, `estado_acceso` (default `concedido` for existing
  rows) and `solicitado_en`;
- unique `(proveedor, sujeto)`.

Old password users stay but cannot sign in; the owner can delete them.

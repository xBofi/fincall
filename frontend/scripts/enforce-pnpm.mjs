/**
 * Refuses lifecycle and application commands started through npm/yarn.
 * The package-manager declaration handles Corepack; this guard also protects
 * direct `npm run …` invocations.
 */
export function assertPnpm(userAgent = process.env.npm_config_user_agent ?? '') {
  if (!userAgent.startsWith('pnpm/')) {
    throw new Error(
      'Este proyecto exige pnpm. Usa `corepack pnpm <comando>` en lugar de npm.',
    )
  }
}

if (process.argv[1]?.endsWith('enforce-pnpm.mjs')) {
  assertPnpm()
}

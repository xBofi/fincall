import { ApiError, type Role } from '../api'

export const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: 'owner', label: 'Propietario', hint: 'Todo, incluida la gestión de usuarios' },
  { value: 'administrator', label: 'Administrator', hint: 'Expedientes, grabaciones y auditoría' },
  { value: 'quality', label: 'Calidad', hint: 'Expedientes y grabaciones' },
]

export const PROVIDER: Record<string, string> = { google: 'Google', microsoft: 'Microsoft' }

export function detail(e: unknown): string {
  return e instanceof ApiError ? e.detail : String(e)
}

import { ApiError, type Rol } from '../api'

export const ROLES: { value: Rol; label: string; ayuda: string }[] = [
  { value: 'owner', label: 'Propietario', ayuda: 'Todo, incluida la gestión de usuarios' },
  { value: 'administrador', label: 'Administrador', ayuda: 'Expedientes, grabaciones y auditoría' },
  { value: 'calidad', label: 'Calidad', ayuda: 'Expedientes y grabaciones' },
]

export const PROVEEDOR: Record<string, string> = { google: 'Google', microsoft: 'Microsoft' }

export function detalle(e: unknown): string {
  return e instanceof ApiError ? e.detalle : String(e)
}

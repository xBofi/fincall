import { useId } from 'react'

/**
 * Marca de Fincall: la voz (tres barras) llega a un punto y sale en tres gestiones; la central,
 * con check, es la llamada resuelta. Un solo degradado de rojo a negro la recorre de izquierda a
 * derecha. En el nombre, el punto de la i es el rojo de la voz.
 */
export default function Logo({ withName = false, className }: { withName?: boolean; className?: string }) {
  const gradient = `fincall-${useId().replace(/:/g, '')}`
  const stroke = `url(#${gradient})`
  return (
    <span className={`brand${className ? ` ${className}` : ''}`} role="img" aria-label="Fincall">
      <svg className="brand-symbol" viewBox="0 0 80 64" aria-hidden="true">
        <defs>
          <linearGradient id={gradient} x1="4" y1="0" x2="76" y2="0" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#ff3b2f" />
            <stop offset=".38" stopColor="#d0122f" />
            <stop offset=".75" stopColor="#5a0f24" />
            <stop offset="1" stopColor="#15121b" />
          </linearGradient>
        </defs>
        <g fill="none" stroke={stroke} strokeWidth="6" strokeLinecap="round">
          <path d="M5 27 V37 M15 19 V45 M25 10 V54" />
          <path d="M38 32 C50 32 48 10 60 10 M38 32 H60 M38 32 C50 32 48 54 60 54" />
        </g>
        <g fill={stroke}>
          <circle cx="38" cy="32" r="6.5" />
          <circle cx="61" cy="10" r="5.5" />
          <circle cx="61" cy="54" r="5.5" />
          <circle cx="67.5" cy="32" r="9.5" />
        </g>
        <path d="M63 32.3 L66.2 35.5 L72 29" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {withName && (
        <span className="brand-name" aria-hidden="true">
          f<span className="brand-i">ı</span>ncall
        </span>
      )}
    </span>
  )
}

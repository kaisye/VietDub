/** VietDub mark (the brand tick) on a blue tile — the blue re-color of assets/vietdub-icon.svg. */
export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="20 20 200 200" aria-hidden="true" className="shrink-0">
      <defs>
        <linearGradient id="vd-mark-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3B82F6" />
          <stop offset="1" stopColor="#1E40AF" />
        </linearGradient>
      </defs>
      <rect x="20" y="20" width="200" height="200" rx="52" fill="url(#vd-mark-bg)" />
      <g transform="translate(34,52) scale(0.46)">
        <path d="M112 124 C 124 214, 144 262, 178 270 C 152 286, 120 248, 98 134 Z" fill="#BFDBFE" opacity="0.55" />
        <path
          d="M96 116 C 116 214, 140 268, 162 274 C 186 278, 214 232, 360 54 C 332 60, 220 196, 168 206 C 144 202, 124 162, 112 112 C 108 106, 100 108, 96 116 Z"
          fill="#FFFFFF"
        />
      </g>
    </svg>
  );
}

/** Jednoduché čiarové ikony (štýl ako vo Workplane), bez ďalšej knižnice. */
const PATHS: Record<string, string[]> = {
  home: ['M3 10.5 12 3l9 7.5', 'M5 9.5V21h14V9.5', 'M10 21v-6h4v6'],
  route: ['M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15', 'C 6 19 3', 'C 18 5 3'],
  fuel: ['M3 22h12', 'M4 9h10', 'M14 22V4a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v18', 'M14 13h2a2 2 0 0 1 2 2v2a2 2 0 0 0 4 0V9.8a2 2 0 0 0-.6-1.4L18 5'],
  wand: ['M15 4V2', 'M15 16v-2', 'M8 9h2', 'M20 9h2', 'M17.8 11.8 19 13', 'M15 9h.01', 'M17.8 6.2 19 5', 'M3 21l9-9', 'M12.2 6.2 11 5'],
  report: ['M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z', 'M14 2v5h6', 'M8 13h8', 'M8 17h8', 'M8 9h2'],
  car: ['M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2', 'M9 17h6', 'C 7 17 2', 'C 17 17 2'],
  settings: ['C 12 12 3', 'M12 2v3', 'M12 19v3', 'M4.9 4.9 7 7', 'M17 17l2.1 2.1', 'M2 12h3', 'M19 12h3', 'M4.9 19.1 7 17', 'M17 7l2.1-2.1'],
  grid: ['R 3 3 7 7', 'R 14 3 7 7', 'R 14 14 7 7', 'R 3 14 7 7'],
  logout: ['M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4', 'm16 17 5-5-5-5', 'M21 12H9'],
  plus: ['M5 12h14', 'M12 5v14'],
  edit: ['M21.2 6.8a2.8 2.8 0 0 0-4-4L3.8 16.2a2 2 0 0 0-.5.8l-1.3 4.4a.5.5 0 0 0 .6.6l4.4-1.3a2 2 0 0 0 .8-.5z', 'm15 5 4 4'],
  trash: ['M3 6h18', 'M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6', 'M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2', 'M10 11v6', 'M14 11v6'],
  left: ['m15 18-6-6 6-6'], right: ['m9 18 6-6-6-6'], down: ['m6 9 6 6 6-6'],
  download: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'm7 10 5 5 5-5', 'M12 15V3'],
  upload: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'm17 8-5-5-5 5', 'M12 3v12'],
  alert: ['m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3', 'M12 9v4', 'M12 17h.01'],
  lock: ['R 3 11 18 11', 'M7 11V7a5 5 0 0 1 10 0v4'],
  pin: ['M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z', 'C 12 10 3'],
  search: ['C 11 11 8', 'm21 21-4.3-4.3'],
  sort: ['m3 16 4 4 4-4', 'M7 20V4', 'm21 8-4-4-4 4', 'M17 4v16'],
  check: ['M20 6 9 17l-5-5'],
  gauge: ['m12 14 4-4', 'M3.3 19a10 10 0 1 1 17.4 0'],
  money: ['R 2 6 20 12', 'C 12 12 2', 'M6 12h.01', 'M18 12h.01'],
  users: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2', 'C 9 7 4', 'M22 21v-2a4 4 0 0 0-3-3.9', 'M16 3.1a4 4 0 0 1 0 7.8'],
  sun: ['C 12 12 4', 'M12 2v2', 'M12 20v2', 'm4.9 4.9 1.4 1.4', 'm17.7 17.7 1.4 1.4', 'M2 12h2', 'M20 12h2', 'm6.3 17.7-1.4 1.4', 'm19.1 4.9-1.4 1.4'],
  moon: ['M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z'],
  monitor: ['R 2 3 20 14', 'M8 21h8', 'M12 17v4'],
  history: ['M3 12a9 9 0 1 0 9-9 9.8 9.8 0 0 0-6.7 2.7L3 8', 'M3 3v5h5', 'M12 7v5l4 2'],
};

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {(PATHS[name] ?? []).map((d, i) => {
      const [kind, ...n] = d.split(' ');
      if (kind === 'C') return <circle key={i} cx={n[0]} cy={n[1]} r={n[2]} />;
      if (kind === 'R') return <rect key={i} x={n[0]} y={n[1]} width={n[2]} height={n[3]} rx={2} />;
      return <path key={i} d={d} />;
    })}
  </svg>;
}

/** Minimal stroke icon set drawn for CareerTwin (24px grid, 1.6 stroke). Decorative: aria-hidden. */
const P: Record<string, string> = {
  today: "M4 5h16v15H4zM4 9h16M9 3v4M15 3v4",
  discover: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  match: "M4 12h4l2-5 4 10 2-5h4",
  evidence: "M6 3h9l3 3v15H6zM9 11h6M9 15h6M9 7h3",
  resume: "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5",
  applications: "M4 5h4v14H4zM10 5h4v9h-4zM16 5h4v6h-4z",
  experiments: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M7.5 15h9",
  atlas: "M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14",
  simulator: "M4 18c4 0 4-12 8-12s4 12 8 12M4 18h16",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
  arrow: "M5 12h14M13 6l6 6-6 6",
  alert: "M12 4l9 16H3zM12 10v4M12 17v.5",
  check: "M5 12l4 4 10-10",
  cross: "M6 6l12 12M18 6L6 18",
  unknown: "M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17v.5M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2",
  bookmark: "M7 4h10v17l-5-4-5 4z",
  filter: "M4 5h16l-6 8v6l-4 2v-8z",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  menu: "M4 7h16M4 12h16M4 17h16",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
};
export function Icon({ name, size = 18, className = "" }: { name: keyof typeof P | string; size?: number; className?: string }) {
  return (
    <svg className={`ic ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={P[name] ?? P.unknown} />
    </svg>
  );
}

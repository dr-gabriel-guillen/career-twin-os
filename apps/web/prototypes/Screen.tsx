import { useEffect, useRef, type ReactNode } from "react";

/** Page frame: sets the title and, after the first screen, moves focus to the heading. */
let first = true;
export function Screen({ title, lead, actions, children }: { title: string; lead?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const h = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    document.title = `${title} — CareerTwin journeys (prototype)`;
    if (first) {
      first = false;
      return;
    }
    h.current?.focus({ preventScroll: true });
  }, [title]);
  return (
    <div className="pt-screen">
      <header className="pt-screen__head">
        <div>
          <h1 ref={h} tabIndex={-1}>
            {title}
          </h1>
          {lead ? <p className="pt-screen__lead">{lead}</p> : null}
        </div>
        {actions ? <div className="pt-screen__actions">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}


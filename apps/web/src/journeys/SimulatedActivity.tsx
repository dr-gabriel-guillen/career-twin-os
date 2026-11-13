import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, TableRegion } from "@careertwin/ui";
import { EVENT_LABELS, groupApplications } from "@/lib/applications";
import { formatDateTime, truncate } from "@/lib/format";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";

const PAGE = 25;

/**
 * The pipeline's experiment run simulates applications (approval, submission, replies) to exercise
 * the approval gate and to produce outcomes for its analysis. They arrive in their own stream
 * (bundle.labEvents) and are shown only here, labelled as simulated. They never count as the
 * person's applications anywhere else.
 */
export function SimulatedActivity({ focus }: { focus: boolean }) {
  const { bundle } = useApp();
  const { jobById } = useIndexes();
  const ref = useRef<HTMLElement>(null);
  const [limit, setLimit] = useState(PAGE);
  const units = useMemo(() => groupApplications(bundle.labEvents), [bundle.labEvents]);
  useEffect(() => {
    if (focus && ref.current) {
      ref.current.scrollIntoView({ block: "start" });
      ref.current.focus({ preventScroll: true });
    }
  }, [focus]);
  if (bundle.labEvents.length === 0) return null;
  const count = (type: string) => bundle.labEvents.filter((e) => e.event_type === type).length;
  const labelled = bundle.labEvents.filter((e) => e.source.startsWith("simulation:")).length;

  return (
    <section className="ex-lab" id="simulated" aria-labelledby="ex-lab-title" tabIndex={-1} ref={ref} data-testid="lab-activity">
      <header className="ex-lab__head">
        <h2 id="ex-lab-title">Simulated activity from the pipeline’s experiment run</h2>
        <Badge tone="synthetic">Simulated</Badge>
      </header>
      <p className="ex-lab__lead">
        The experiment stage simulated these {units.length} applications to test the approval gate and to produce outcomes for the analysis above. <strong>They are not your applications:</strong> nothing was submitted and no employer was contacted. They are kept out of Applications, Discover, Overview, counts and follow-ups.
      </p>
      <dl className="ex-lab__facts">
        <div>
          <dt>Simulated units</dt>
          <dd data-testid="lab-units">{units.length}</dd>
        </div>
        <div>
          <dt>Simulated approvals</dt>
          <dd>{count("approved")}</dd>
        </div>
        <div>
          <dt>Simulated submissions</dt>
          <dd>{count("submitted")}</dd>
        </div>
        <div>
          <dt>Synthetic replies</dt>
          <dd>{count("recruiter_screen") + count("interview") + count("offer") + count("rejected")}</dd>
        </div>
      </dl>
      <p className="ex-lab__note">
        How CareerTwin knows: these records arrive in the experiment run’s own file (<code>experiment_events.json</code>), not in the application log. {labelled === bundle.labEvents.length ? "Each record is also labelled as a simulation by the pipeline." : `${bundle.labEvents.length - labelled} of these records do not label themselves as simulated; they are kept apart all the same, because the file they arrive in is what decides.`}
      </p>
      <TableRegion label="Simulated units">
        <table className="ct-table" data-testid="lab-table">
          <thead>
            <tr>
              <th scope="col">Unit</th>
              <th scope="col">Posting</th>
              <th scope="col">Last simulated step</th>
              <th scope="col">When (simulated clock)</th>
            </tr>
          </thead>
          <tbody>
            {units.slice(0, limit).map((u) => (
              <tr key={u.id}>
                <td>
                  <code>{truncate(u.id, 36)}</code>
                </td>
                <td>{truncate(jobById.get(u.job_id)?.title ?? u.job_id, 48)}</td>
                <td>{EVENT_LABELS[u.lastEvent.event_type]}</td>
                <td>{formatDateTime(u.lastEvent.occurred_at ?? u.lastEvent.recorded_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableRegion>
      {units.length > limit ? (
        <Button size="sm" onClick={() => setLimit(limit + PAGE)}>
          Show {Math.min(PAGE, units.length - limit)} more of {units.length - limit}
        </Button>
      ) : null}
    </section>
  );
}

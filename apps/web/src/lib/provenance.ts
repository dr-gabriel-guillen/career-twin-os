/**
 * Where an application event came from, decided by the stream it arrived in, never by its id or its
 * `actor` field (both are written by whoever produced the record and prove nothing on their own).
 *
 * - "recorded": appended in this browser through the workspace reducer, i.e. something the person
 *   did here. Only these are ever described as "You recorded".
 * - "sample":   part of the data the app was started with (bundled demo fixtures, or the pipeline's
 *   events.json). Shown as demo or sample activity, whatever its actor field says.
 *
 * Simulated experiment-run events never reach the application log at all: they arrive in a separate
 * stream (`bundle.labEvents`) and are only shown in the Experiments screen.
 */
import type { DataBundle } from "@/adapters";
import type { ApplicationEvent } from "@/contracts/types";

export type EventOrigin = "recorded" | "sample";

export function originOf(bundle: Pick<DataBundle, "applicationEvents">): (e: ApplicationEvent) => EventOrigin {
  const seeded = new Set(bundle.applicationEvents.map((e) => e.id));
  return (e) => (seeded.has(e.id) ? "sample" : "recorded");
}

/** Who did it, in words that never overstate: sample data is never "you". */
export function attribution(e: ApplicationEvent, origin: EventOrigin, artifacts: boolean): string {
  if (origin === "sample") {
    const where = artifacts ? "Pipeline sample data" : "Demo data";
    if (e.actor === "user") return `${where}, written as a user action`;
    if (e.actor === "system") return `${where}, written by the system`;
    return `${where}, imported`;
  }
  if (e.actor === "user") return "You recorded this here";
  if (e.actor === "system") return "Recorded by CareerTwin";
  return "Imported here";
}

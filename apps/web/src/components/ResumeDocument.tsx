import type { ResumeVariant } from "@/adapters/proposed";
import type { ProfileClaim } from "@/contracts/types";
import "@/pages/resume.css";

const BASIS: Record<string, string> = {
  supported: "Backed by evidence",
  "self-reported": "Self-reported",
  unverified: "Unverified",
  contradicted: "Contradicted",
};

/**
 * Read-only résumé page in document typography. Shows exactly the text that would be approved and
 * exported, with a quiet note under each line saying what it rests on. Lines added in this draft are
 * marked so the person sees what changed on the page itself.
 */
export function ResumeDocument({ variant, name, role, claimById, added = new Set<string>(), testId }: { variant: ResumeVariant; name: string; role: string; claimById: Map<string, ProfileClaim>; added?: Set<string>; testId?: string }) {
  return (
    <article className="rs-paper rs-paper--read" aria-label={`Résumé document for ${role}`} data-testid={testId}>
      <header className="rs-paper__head">
        <h3 className="rs-paper__name">{name}</h3>
        <p className="rs-paper__role">{role}</p>
      </header>
      {variant.sections.map((s) => (
        <section key={s.id} className="rs-section" aria-label={s.heading}>
          <h4 className="rs-heading">{s.heading}</h4>
          {s.bullets.length === 0 ? <p className="rs-empty">No lines in this section.</p> : null}
          <ul className="rs-doclines">
            {s.bullets.map((b) => {
              const basis = b.claim_ids.map((id) => claimById.get(id)?.status).filter((x): x is ProfileClaim["status"] => Boolean(x));
              return (
                <li key={b.id} className={added.has(b.id) ? "rs-docline rs-docline--added" : "rs-docline"}>
                  <span className="rs-docline__text">{b.text}</span>
                  <span className="rs-docline__basis">
                    {added.has(b.id) ? "Added in this draft. " : ""}
                    {basis.length === 0 ? "No claim linked" : [...new Set(basis.map((x) => BASIS[x] ?? x))].join(", ")}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </article>
  );
}

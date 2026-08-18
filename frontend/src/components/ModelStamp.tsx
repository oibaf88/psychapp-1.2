import { modelProvenanceLabel } from "../api";

/**
 * Which model produced one stored interaction.
 *
 * A patient's history can span endpoints — an analysis from March under
 * Claude, one from April under a model running on the clinician's own
 * machine — and the two are not interchangeable evidence: a weaker model
 * missing a linguistic marker looks exactly like the marker not being there.
 * So every assistant turn and every analysis carries its own provenance
 * rather than inheriting whatever is configured today.
 *
 * When nothing is recorded the stamp says exactly that, and no more. Two
 * different situations land there — a turn assembled from the server-owned
 * safety templates, which never had a model behind it, and a row written
 * before provenance was recorded at all — and the stored data does not
 * separate them. Guessing between them, or backfilling either with today's
 * setting, would put an inference where a record belongs.
 */
export default function ModelStamp({
  message,
  unknownLabel = "sin modelo registrado",
}: {
  message: { provider?: string | null; model?: string | null; provider_base_url?: string | null };
  unknownLabel?: string;
}) {
  const label = modelProvenanceLabel(message);
  if (!label) return <span className="model-stamp">{unknownLabel}</span>;
  return (
    <span className={message.provider === "openai_compatible" ? "model-stamp is-local" : "model-stamp"}>
      {label}
    </span>
  );
}

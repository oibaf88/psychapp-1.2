import { CSSProperties, useId } from "react";
import {
  COMPACT_CURVES,
  COMPACT_STROKE,
  MARK_CLIP,
  MARK_STOPS,
  MARK_VIEWBOX,
  MASTER_CURVES,
  MASTER_STROKE,
} from "./markGeometry.generated";

interface Props {
  /**
   * "master" is the 13-curve drawing; below roughly 64px it fills in, so
   * anything that small takes "compact" — 7 curves on a heavier stroke.
   */
  variant?: "master" | "compact";
  className?: string;
}

/**
 * The PsychDeep mark: the app's wave metaphor folded into a disc.
 *
 * The geometry comes from markGeometry.generated.ts, which the icon script
 * writes at the same time as the PNG and SVG icons — the logo in the chrome,
 * the icon on the home screen and the breathing pacer are one drawing.
 *
 * Every curve carries its own `--pd-mark-shift`, the direction and share of
 * the outward travel it takes when the mark breathes. Inner curves travel
 * further than outer ones, so the dilation reads as depth rather than as a
 * scale. The stylesheet decides how far and how fast; this component only
 * says which curves exist.
 *
 * Decorative: the mark is aria-hidden everywhere, and what it means is
 * carried by text next to it.
 */
export default function PsychDeepMark({ variant = "master", className }: Props) {
  // useId() emits colons, which are legal in an id but awkward inside url().
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const curves = variant === "master" ? MASTER_CURVES : COMPACT_CURVES;
  const stroke = variant === "master" ? MASTER_STROKE : COMPACT_STROKE;

  return (
    <svg
      className={className ? `pd-mark ${className}` : "pd-mark"}
      viewBox={MARK_VIEWBOX}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={`${id}-gradient`} gradientUnits="userSpaceOnUse" x1="0" y1="3" x2="0" y2="61">
          {MARK_STOPS.map(([offset, color]) => (
            <stop key={offset} offset={offset} stopColor={color} />
          ))}
        </linearGradient>
        <clipPath id={`${id}-clip`}>
          <circle cx={MARK_CLIP.cx} cy={MARK_CLIP.cy} r={MARK_CLIP.r} />
        </clipPath>
      </defs>
      <g
        clipPath={`url(#${id}-clip)`}
        fill="none"
        stroke={`url(#${id}-gradient)`}
        strokeWidth={stroke}
        strokeLinecap="round"
      >
        {curves.map((curve, index) => (
          <path
            key={index}
            className="pd-mark__curve"
            d={curve.d}
            opacity={curve.opacity}
            style={{ "--pd-mark-shift": curve.shift } as CSSProperties}
          />
        ))}
      </g>
    </svg>
  );
}

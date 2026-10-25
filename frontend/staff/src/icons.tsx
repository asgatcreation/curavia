/**
 * Icons, hand-drawn rather than pulled from a package.
 *
 * Nine icons do not justify a dependency, a licence and 40 kB, and these are
 * drawn on the same 24-unit grid with the same 1.75 stroke so they sit evenly
 * in a sidebar. Every one is aria-hidden: each is paired with a text label,
 * because a nurse covering a shift in a department she does not normally work
 * in should not have to learn a pictogram.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-5 shrink-0"
      {...rest}
    >
      {children}
    </svg>
  );
}

export function IconDesk(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 9h18M4 9V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3M6 9v11M18 9v11M9 13h6" />
    </Icon>
  );
}

export function IconStethoscope(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3v5a4 4 0 0 0 8 0V3M6 3H4m2 0h2m6 0h2m-2 0h-2m-4 9v2a6 6 0 0 0 6 6 4 4 0 0 0 4-4v-2" />
      <circle cx="18" cy="12" r="2" />
    </Icon>
  );
}

export function IconPill(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="2.5" y="8.5" width="19" height="7" rx="3.5" transform="rotate(-45 12 12)" />
      <path d="M8.5 8.5l7 7" />
    </Icon>
  );
}

export function IconBoard(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9h18M9 9v11M15 9v11" />
    </Icon>
  );
}

export function IconChart(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 20V4M4 20h16M8 17v-5M12 17V8M16 17v-7M20 17v-3" />
    </Icon>
  );
}

export function IconSearch(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </Icon>
  );
}

export function IconSun(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
    </Icon>
  );
}

export function IconMoon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 13.5A8 8 0 0 1 10.5 4a8 8 0 1 0 9.5 9.5Z" />
    </Icon>
  );
}

export function IconDisplay(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </Icon>
  );
}

export function IconChevron(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m6 9 6 6 6-6" />
    </Icon>
  );
}

export function IconWarning(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4M12 17h.01" />
    </Icon>
  );
}

export function IconUsers(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="3.5" />
      <path d="M22 20v-2a4 4 0 0 0-3-3.87M16.5 3.6a4 4 0 0 1 0 6.8" />
    </Icon>
  );
}

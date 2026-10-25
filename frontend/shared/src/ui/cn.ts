/**
 * Join class names, dropping anything falsy.
 *
 * Deliberately not `clsx` or `tailwind-merge`. This is eight lines, and the
 * components below never pass two conflicting Tailwind classes for the same
 * property — the variant maps are exhaustive, so there is nothing to merge.
 */
export type ClassValue = string | false | null | undefined;

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}

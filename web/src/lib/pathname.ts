export function pathnameStartsWith(
  pathname: string | null | undefined,
  prefixes: readonly string[],
): boolean {
  if (!pathname) {
    return false;
  }

  return prefixes.some((prefix) => pathname.startsWith(prefix));
}

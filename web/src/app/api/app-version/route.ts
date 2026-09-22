export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({
    buildId: process.env.NEXT_PUBLIC_WEB_BUILD_ID,
    version: process.env.NEXT_PUBLIC_APP_VERSION,
  }, { headers: { "Cache-Control": "no-store, max-age=0" } });
}

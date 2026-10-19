import { NextResponse, type NextRequest } from "next/server";

/** Coarse gate for the application area: without any session cookie, go to the sign-in page.
 * Real authorisation is always enforced server-side by the API on every request. */
export function proxy(req: NextRequest) {
  const hasSession = req.cookies.has("bv_access") || req.cookies.has("bv_csrf");
  if (!hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", req.nextUrl.pathname + req.nextUrl.search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/app/:path*"] };

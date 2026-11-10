import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  if (!req.cookies.get("refresh_token")) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(req.nextUrl.pathname)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/dashboard/:path*", "/team/:path*", "/audit/:path*", "/security/:path*", "/documents/:path*", "/upload/:path*", "/jobs/:path*", "/requirements/:path*", "/review/:path*", "/suppliers/:path*", "/rfqs/:path*", "/reference/:path*", "/assistant/:path*"] };

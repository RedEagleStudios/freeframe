/**
 * Mount the app under a sub-path (e.g. /freeframe) instead of the domain root.
 * Build-time: set NEXT_PUBLIC_BASE_PATH and rebuild the web image. Empty by
 * default, so root deployments are unchanged.
 */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ''

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  output: 'standalone',
  images: {
    remotePatterns: [
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '9000',
      },
    ],
  },
  // One click on the consent page gives an AI app access to the account, so no
  // other site may frame it (clickjacking).
  async headers() {
    return [
      {
        source: '/oauth/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Content-Security-Policy', value: "frame-ancestors 'none'" },
        ],
      },
    ]
  },
}

module.exports = nextConfig

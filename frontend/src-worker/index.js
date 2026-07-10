// Static-assets Worker in front of the Vite SPA build.
//
// Two jobs:
//  1. Force https:// - the SPA is served fine over plain http, but the API's
//     CORS allowlist (rightly) only contains https origins, so an http visit
//     looks like "Failed to fetch" on every API call. Redirect before serving.
//  2. Per-route SEO meta injection - because this is a single-page app, every
//     deep link (a shared blog post, a shared ship) is served the SAME static
//     index.html, so link-preview crawlers (Slack / WhatsApp / X / LinkedIn /
//     Google) would otherwise see one generic card for every URL. We can't run
//     React server-side, but we CAN rewrite the <title> and OG/Twitter meta
//     tags in the served HTML with HTMLRewriter so each deep link gets its own
//     rich preview. Unknown paths fall through to the default meta baked into
//     index.html.

const SITE = 'https://darkships.org';
const DEFAULT_OG_IMAGE = `${SITE}/og-image.png`;

const escapeHtml = (s) =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

// Turn a URL slug ("shadow-fleet-tankers") into a readable title
// ("Shadow Fleet Tankers"). Best-effort only - we can't read the post body
// server-side (posts have no content files), so we humanise the slug and fall
// back to a generic blog description. See TODO below.
const humaniseSlug = (slug) =>
  decodeURIComponent(slug)
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());

// Resolve the per-route meta overrides for a given URL, or null to keep the
// static defaults from index.html.
function metaForRoute(url) {
  const path = url.pathname.replace(/\/+$/, '') || '/';

  // Shared ship links. Two shapes are supported:
  //   /ship/<mmsi>        (path style)
  //   /?ship=<mmsi>       (query style - used by the shareable ship link)
  let mmsi = null;
  const shipPath = path.match(/^\/ship\/(\d{7,9})$/);
  if (shipPath) mmsi = shipPath[1];
  else if (path === '/') {
    const q = url.searchParams.get('ship');
    if (q && /^\d{7,9}$/.test(q)) mmsi = q;
  }
  if (mmsi) {
    const canonical = shipPath ? `${SITE}/ship/${mmsi}` : `${SITE}/?ship=${mmsi}`;
    return {
      title: `Vessel ${mmsi} on Dark Ships`,
      description:
        `Live risk profile and last-known position for vessel MMSI ${mmsi} on Dark Ships - ` +
        `sanctions, shadow-fleet, dark-activity and AIS-spoofing signals from free AIS and satellite data.`,
      url: canonical,
      image: DEFAULT_OG_IMAGE,
      type: 'website',
    };
  }

  // Blog posts: /blog/<slug>. We can't read the post body server-side, so this
  // is a best-effort card built from the slug plus a generic blog description.
  // TODO: when posts get real content files, look the post up here (e.g. from a
  // build-time generated manifest bound to the Worker) for an exact title,
  // description and per-post OG image.
  const blogPost = path.match(/^\/blog\/([^/]+)$/);
  if (blogPost) {
    const title = humaniseSlug(blogPost[1]);
    return {
      title: `${title} - Dark Ships Blog`,
      description:
        'Analysis and field notes from Dark Ships on shadow-fleet tankers, sanctions evasion, ' +
        'AIS spoofing, narco vessels and illegal (IUU) fishing - built from free AIS and satellite data.',
      url: `${SITE}/blog/${blogPost[1]}`,
      image: DEFAULT_OG_IMAGE,
      type: 'article',
    };
  }

  // Blog index.
  if (path === '/blog') {
    return {
      title: 'Blog - Dark Ships',
      description:
        'Analysis and field notes from Dark Ships on shadow-fleet tankers, sanctions evasion, ' +
        'AIS spoofing, narco vessels and illegal (IUU) fishing - built from free AIS and satellite data.',
      url: `${SITE}/blog`,
      image: DEFAULT_OG_IMAGE,
      type: 'website',
    };
  }

  // Sources / methodology.
  if (path === '/sources') {
    return {
      title: 'Sources & data - Dark Ships',
      description:
        'The free and open data behind Dark Ships: AIS feeds, sanctions and shadow-fleet lists, ' +
        'port-control detentions and open satellite imagery used to flag high-risk vessels.',
      url: `${SITE}/sources`,
      image: DEFAULT_OG_IMAGE,
      type: 'website',
    };
  }

  return null;
}

// HTMLRewriter handlers that overwrite the head tags with per-route values.
class TitleRewriter {
  constructor(v) { this.v = v; }
  element(el) { el.setInnerContent(this.v); }
}
class AttrRewriter {
  constructor(attr, v) { this.attr = attr; this.v = v; }
  element(el) { el.setAttribute(this.attr, this.v); }
}

function injectMeta(response, meta) {
  const title = escapeHtml(meta.title);
  const description = escapeHtml(meta.description);
  const ogUrl = escapeHtml(meta.url);
  const image = escapeHtml(meta.image);
  const type = escapeHtml(meta.type || 'website');

  return new HTMLRewriter()
    .on('title', new TitleRewriter(title))
    .on('link[rel="canonical"]', new AttrRewriter('href', ogUrl))
    .on('meta[name="description"]', new AttrRewriter('content', description))
    .on('meta[property="og:title"]', new AttrRewriter('content', title))
    .on('meta[property="og:description"]', new AttrRewriter('content', description))
    .on('meta[property="og:url"]', new AttrRewriter('content', ogUrl))
    .on('meta[property="og:image"]', new AttrRewriter('content', image))
    .on('meta[property="og:type"]', new AttrRewriter('content', type))
    .on('meta[name="twitter:title"]', new AttrRewriter('content', title))
    .on('meta[name="twitter:description"]', new AttrRewriter('content', description))
    .on('meta[name="twitter:image"]', new AttrRewriter('content', image))
    .transform(response);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.protocol === 'http:') {
      url.protocol = 'https:';
      return Response.redirect(url.toString(), 301);
    }

    const response = await env.ASSETS.fetch(request);

    // Only rewrite HTML documents (the SPA shell), and only for GET requests
    // that resolved to a known dynamic route. Everything else - JS, CSS,
    // images, sitemap.xml, robots.txt - is served untouched.
    const isHtml = (response.headers.get('content-type') || '').includes('text/html');
    if (request.method === 'GET' && isHtml && response.status === 200) {
      const meta = metaForRoute(url);
      if (meta) return injectMeta(response, meta);
    }

    return response;
  },
};

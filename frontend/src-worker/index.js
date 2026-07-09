// Force https:// - the SPA is served fine over plain http, but the API's
// CORS allowlist (rightly) only contains https origins, so an http visit
// looks like "Failed to fetch" on every API call. Redirect before serving.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.protocol === 'http:') {
      url.protocol = 'https:';
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};

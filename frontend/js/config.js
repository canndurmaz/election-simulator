/**
 * Where the game server lives.
 *
 * In a browser the frontend and the FastAPI server share an origin, so every path can stay
 * relative. Inside the Android build they do not: Capacitor serves these same files from
 * `https://localhost` *inside the WebView*, so `/api/maps` would resolve to the app bundle
 * instead of the server. (The Python backend cannot ship in an APK, so there is always a
 * remote server.) Everything that leaves the app therefore goes through `url()` / `wsUrl()`.
 *
 * The address is resolved in priority order:
 *   1. what the player typed on the in-app setup screen (localStorage)
 *   2. `window.__ELECTION_SERVER__` baked in at build time by `server-config.js`
 *   3. the page's own origin -- the browser case, where nothing needs configuring
 *
 * On native with none of the above, `needsSetup()` is true and app.js shows the setup screen.
 */
const trim = u => String(u ?? '').trim().replace(/\/+$/, '');

export const NATIVE = !!globalThis.Capacitor?.isNativePlatform?.();
const BUILD_SERVER = trim(globalThis.__ELECTION_SERVER__);
const BUILD_WEB = trim(globalThis.__ELECTION_WEB__);
const KEY = 'server';

const read = k => { try { return localStorage.getItem(k); } catch { return null; } };

/** Base origin of the game server, or '' when the app still needs configuring. */
export function serverUrl() {
  return trim(read(KEY)) || BUILD_SERVER || (NATIVE ? '' : location.origin);
}

export function setServerUrl(u) {
  const v = trim(u);
  try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch {}
  return v;
}

/** True when we are packaged and have no address to talk to yet. */
export const needsSetup = () => NATIVE && !serverUrl();

/** Absolute URL for an API path. */
export const url = path => serverUrl() + path;

/** ws:// for http://, wss:// for https:// -- Android blocks cleartext, so real devices need https. */
export function wsUrl(code, token) {
  const base = serverUrl() || location.origin;
  return `${base.replace(/^http/, 'ws')}/ws/${code}?token=${encodeURIComponent(token)}`;
}

/**
 * Base for the invite link players share. Not the same as `serverUrl()` in general: the app
 * talks to an API host, but a friend tapping the link needs the *web* build. Falls back to
 * the server (which serves the web build too, in the default single-host deployment).
 */
export const shareBase = () => BUILD_WEB || serverUrl() || location.origin;

/**
 * Check an address before saving it. Resolves to the /api/health payload or throws.
 * The timeout matters: a wrong host otherwise hangs until the OS gives up.
 */
export async function probe(candidate, ms = 6000) {
  const base = trim(candidate);
  if (!/^https?:\/\/.+/i.test(base)) throw new Error('Enter a full address, e.g. https://election.example.com');
  const stop = AbortSignal.timeout ? AbortSignal.timeout(ms) : undefined;
  let res;
  try {
    res = await fetch(`${base}/api/health`, { signal: stop, cache: 'no-store' });
  } catch {
    throw new Error("Could not reach that address. Check the URL and that the server is running.");
  }
  if (!res.ok) throw new Error(`Server answered ${res.status} — is that the game server?`);
  const data = await res.json().catch(() => null);
  if (!data?.ok) throw new Error('That address answered, but it is not an Election Night server.');
  return data;
}

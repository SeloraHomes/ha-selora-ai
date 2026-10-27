// Links into Frigate's own UI: an event, or a camera's zones and masks. Frigate
// does these well; the panel only takes the user to the right page.
//
// Frigate's UI reaches the browser through a Home Assistant app with ingress
// (the Frigate app, or Frigate Proxy for a Frigate on another host), found by
// its sidebar panel, so no app list (an admin-only call) is needed. HA's own
// app panel loads the app's root and drops any sub-path, so the link opens the
// ingress URL itself, in a new tab, after creating the ingress session cookie
// the way HA's frontend does before it loads an app.

const FRIGATE = /frigate/i;

/** The slug of the app serving Frigate's UI, or null when there is none. */
export function frigateApp(hass) {
  for (const panel of Object.values(hass?.panels || {})) {
    const slug = panel?.config?.addon || panel?.config?.ingress;
    if (typeof slug === "string" && FRIGATE.test(slug)) return slug;
  }
  return null;
}

/** The page in Frigate's UI for an event. */
export function eventPage(eventId) {
  return `explore?event_id=${encodeURIComponent(eventId)}`;
}

/** The page in Frigate's UI where a camera's zones and masks are drawn. */
export function zonesPage(camera) {
  return `settings?page=masksAndZones&camera=${encodeURIComponent(camera)}`;
}

function setIngressCookie(session) {
  document.cookie = `ingress_session=${session};path=/api/hassio_ingress/;SameSite=Strict${
    location.protocol === "https:" ? ";Secure" : ""
  }`;
}

/**
 * Open *page* of Frigate's UI in a new tab. The tab is opened during the click,
 * before any await, or the browser blocks it as a pop-up.
 */
export async function openFrigatePage(host, page) {
  const slug = frigateApp(host.hass);
  if (!slug) return;
  const tab = window.open("", "_blank");
  try {
    const info = await host.hass.callWS({
      type: "supervisor/api",
      endpoint: `/addons/${slug}/info`,
      method: "get",
    });
    const entry = info?.ingress_entry;
    if (!entry) throw new Error("the app has no ingress");
    const { session } = await host.hass.callWS({
      type: "supervisor/api",
      endpoint: "/ingress/session",
      method: "post",
    });
    setIngressCookie(session);
    if (tab) tab.location.href = `${entry.replace(/\/+$/, "")}/${page}`;
  } catch (err) {
    console.error("Selora AI: could not open Frigate", err);
    tab?.close();
  }
}
